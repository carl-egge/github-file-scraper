#-------------------------------------------------------------------------------
# MIT License
#
# Copyright (c) 2019 Michael Schröder, Jürgen Cito, Carl Egge, Stefan Schulte, 
# Avik Banerjee
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#-------------------------------------------------------------------------------

############################  GITHUB FILE SCRAPER  #############################

## VERSION: Search for Solidity Smart Contracts and Flatten them

# This script is a modification of the github-searcher from Michael Schröder and
# Jürgen Cito. It exhaustively samples GitHub Repo Search results and stores
# Solidity files including their commit history and their content.
# This script was developed by Carl Egge on behalf of the Christian Doppler Labor.
# Its main purpose is to build a local database of Solidity smart contracts and
# their versions. It is structured in a semi-chronological, readable form.

# TODO (Carl Egge):
# - Add a function to flatten the Solidity files and store them in a separate file
# - Add a function to extract the compiler version from the Solidity files
# - use git clone or different method to download all Solidity files from a repo
#   (must keep folder structure of repo)
# - switch from sqlite to mongodb
# - use flattener to flatten the downloaded Solidity files -> skip if fails
# - clean up directory structure after each repository 
# - add license check
# - maybe update find_compiler_version function

# Idea: 
# 1. Clone a repository into "repo" folder
# 2. Find all Solidity files in the repository
# 3. For each Solidity file, get the commit history
# 4. For each commit, flatten the Solidity file
# 5. Store the flattened content in the commit history

import os, sys, argparse, shutil, time, signal, re
import csv, pymongo
import requests
import git, subprocess

# Before we get to the fun stuff, we need to parse and validate arguments, check
# environment variables, set up the help text and so on.

# fix for argparse: ensure terminal width is determined correctly
os.environ['COLUMNS'] = str(shutil.get_terminal_size().columns)

parser = argparse.ArgumentParser(
    formatter_class=argparse.RawDescriptionHelpFormatter,
    description='''Exhaustively sample the GitHub Code Search API and 
store files and commits of Solidity smart contracts.''')

parser.add_argument('--database', metavar='STRING', default='mongodb://localhost:27017/', 
    help='connection string for target mongodb database (default: mongodb://localhost:27017/)')

parser.add_argument('--statistics', metavar='FILE', default='sampling.csv', 
    help='sampling statistics file (default: sampling.csv)')

parser.add_argument('--min-size', metavar='BYTES', type=int, default=1, 
    help='minimum code file size (default: 1)')

# Only files smaller than 384 KB are searchable via the GitHub API.
MAX_FILE_SIZE = 393216

parser.add_argument('--max-size', metavar='BYTES', type=int, 
    default=MAX_FILE_SIZE, 
    help=f'maximum code file size (default: {MAX_FILE_SIZE})')

parser.add_argument('--stratum-size', metavar='BYTES', type=int, default=5,
    help='''length of file size ranges into which population is partitioned 
    (default: 5)''')

parser.add_argument('--no-throttle', dest='throttle', action='store_false', 
    help='disable request throttling')

parser.add_argument('--search-forks', dest='forks', action='store_true', 
    help='''add 'fork:true' to query which includes forked repos in the result''')

parser.add_argument('--license-filter', dest='licensed', action='store_true', 
    help='filter the query with a list of open source licenses')

parser.add_argument('--github-token', metavar='TOKEN', 
    default=os.environ.get('GITHUB_TOKEN'), 
    help='''personal access token for GitHub 
    (by default, the environment variable GITHUB_TOKEN is used)''')

args = parser.parse_args()

if args.min_size < 1:
    sys.exit('min-size must be positive')
if args.min_size >= args.max_size:
    sys.exit('min-size must be less than or equal to max-size')
if args.max_size < 1:
    sys.exit('max-size must be positive')
if args.max_size > MAX_FILE_SIZE:
    sys.exit(f'max-size must be less than or equal to {MAX_FILE_SIZE}')
if args.stratum_size < 1:
    sys.exit('stratum-size must be positive')
if not args.github_token:
    confirm_no_token = input('''No GitHub TOKEN was specified or found in the environment variables.
Do you want to run the program without a token (this will slow the program down)? [y/N]\n''')
    if confirm_no_token.lower() == 'yes' or confirm_no_token.lower() == 'y':
        print("\nThe program will now run without a TOKEN (ratelimit at 60 requests per hour).\n")
        time.sleep(2)
    else:
        sys.exit("\nYou can specifiy a personal access token for GitHub using the '--github-token' argument.")
            

#-------------------------------------------------------------------------------

# The GitHub Code Search API is limited to 1000 results per query. To get around
# this limitation, we can take advantage of the ability to restrict searches to
# files of a certain size. By repeatedly searching with the same query but
# different size ranges, we can reach a pretty good sample of the overall
# population. This is a technique known as *stratified sampling*. The strata in
# our case are non-overlapping file size ranges.

# Let's start with some global definitions. We need to keep track of the first
# and last size in the current stratum...

strat_first = args.min_size
strat_last = min(args.min_size + args.stratum_size - 1, args.max_size)

# ...as well as the current stratum's population of repositories and the amount
# of repositories/files/commits that have been sampled so far (in the current 
# stratum). A value of -1 indicates "unknown".

pop_repo = -1
sam_repo = -1
sam_file = -1
sam_comit = -1

# We also keep track of the total (cumulative) sample sizes so far, and we store 
# it for the downloaded repos/files/commits respectivley.

total_sam_repo = -1
total_sam_file = -1
total_sam_comit = -1

# We also want to keep track of the execution time of the script, therefore we 
# store the starting time. Additionally we store the ratelimit-used information
# to keep track of how many api_calls we can still use. And just for information 
# we count the total amount of github api calls that have been made.

start = time.time()
rate_used = 0
api_calls = 0

# Here we store list of opensource liscense keys for GitHub API. Please note that
# this list includes viral licenses that require a user to include the same license
# in a project if a specific file from the result set should be modified and 
# redistributed.

licenses = ['apache-2.0', 'agpl-3.0', 'bsd-2-clause', 'bsd-3-clause', 'bsl-1.0',
            'cc0-1.0', 'epl-2.0', 'gpl-2.0', 'gpl-3.0', 'lgpl-2.1', 'mit',
            'mpl-2.0', 'unlicense']
current_license = ''
current_cumulative_pop = 0

# Display the current repository
current_repo = ''
# Ensure the 'repo' directory exists
repo_directory = "repo"
if not os.path.exists(repo_directory):
    os.makedirs(repo_directory)

#-------------------------------------------------------------------------------
# In order to store our results we must first connect to the results database.
# In this version of the script we use a MongoDB database. We also create a
# collection in the database to store our results.

try:
    print(' > Trying to connect to MongoDB database: "%s"' % args.database)
    # client = MongoClient('mongodb://<username>:<password>@<host>:<port>/')
    mongo_client = pymongo.MongoClient(args.database)
    mongo_client.server_info() # check if connection is successful
except pymongo.errors.ServerSelectionTimeoutError as e:
    error_string = 'MongoDB connection failed', str(e)
    sys.exit(error_string)

mongo_db = mongo_client.main_db
mongo_collection = mongo_db.testcontracts

sys.stdout.write('\033[F\r\033[J')
print('\n > Successfully connected to MongoDB database: "%s"\n' % args.database)


#-------------------------------------------------------------------------------

# During the search we want to display a table of all the strata sampled so far,
# plus the stratum currently being sampled, some summary information, and a
# status message. These last three items will be continuously updated to signal
# the progress that's being made.

# First, let's just print the table header.

print('                 ┌────────────┬────────────┬────────────┬────────────┐')
print('                 │  pop repo  │  sam repo  │  sam file  │ sam commit │')
print('                 ├────────────┼────────────┼────────────┼────────────┤')

# Now we define some functions to print information about the current stratum.
# By default, this will simply add a new line to the output. However, to be able
# to show live progress, there is also an option to overwrite the current line.

def print_stratum(overwrite=False):
    if overwrite:
        sys.stdout.write('\033[F\r\033[J')
    if strat_first == strat_last:
        size = '%d' % strat_first
    else:
        size = '%d .. %d' % (strat_first, strat_last)
    pop_str = str(pop_repo) if pop_repo > -1 else ''
    sam_repo_str = str(sam_repo) if sam_repo > -1 else ''
    sam_file_str = str(sam_file) if sam_file > -1 else ''
    sam_comit_str = str(sam_comit) if sam_comit > -1 else ''
    per = '%6.2f%%' % (sam_repo/pop_repo*100) if pop_repo > 0 else ''
    print('%16s │ %10s │ %10s │ %10s │ %10s │ %6s' % (size, pop_str, sam_repo_str, 
        sam_file_str, sam_comit_str, per))

# Another function will print the footer of the table, including summary
# statistics and the status message. Here we provide a separate function to
# clear the footer again.

status_msg = ''

def print_footer():
    if args.min_size == args.max_size:
        size = '%d' % args.min_size
    else:
        size = '%d .. %d' % (args.min_size, args.max_size)
    ratelimit = 60 if not args.github_token else 5000
    tot_sam_repo_str = str(total_sam_repo) if total_sam_repo > -1 else ''
    tot_sam_file_str = str(total_sam_file) if total_sam_file > -1 else ''
    tot_sam_comit_str = str(total_sam_comit) if total_sam_comit > -1 else ''
    print('                 ├────────────┼────────────┼────────────┼────────────┤')
    print('                 │  pop repo  │  sam repo  │  sam file  │ sam commit │')
    print('                 └────────────┴────────────┴────────────┴────────────┘')
    print('%16s   %10s   %10s   %10s   %10s   %6s' % (size, '', tot_sam_repo_str,
        tot_sam_file_str, tot_sam_comit_str, ''))
    print()
    print('Current queried license: ', current_license) if args.licensed else print()
    print('Current GitHub ratelimit: %d / ~%d' % (rate_used, ratelimit))
    print('Current repo: ', current_repo)
    print()
    print(status_msg)

def clear_footer():
    sys.stdout.write(f'\033[10F\r\033[J')

# For convenience, we also have function for just updating the status message.
# It returns the old message so it can be restored later if desired.

def update_status(msg):
    global status_msg
    old_msg = status_msg
    status_msg = msg
    sys.stdout.write('\033[F\r\033[J')
    print(status_msg)
    return old_msg

#-------------------------------------------------------------------------------

# To access the GitHub API, we define a little helper function that makes an
# authorized GET request and throttles the number of requests per second so as
# not to run afoul of GitHub's rate limiting. Should a rate limiting error occur
# nonetheless, the function waits the appropriate amount of time before
# automatically retrying the request.

def get(url, params={}):
    global api_calls, rate_used
    if args.throttle:
        # throttle requests to ~5000 per hour
        sleep = 60 if not args.github_token else 0.72
        time.sleep(sleep)
    auth_headers = {} if not args.github_token else {'Authorization': f'token {args.github_token}'}
    try:
        res = requests.get(url, params, headers=auth_headers)
    except requests.ConnectionError:
        print("\nERROR :: There seems to be a problem with your internet connection.")
        return signal_handler(0,0)
    api_calls += 1
    rate_used = (int(res.headers.get('X-RateLimit-Used')) if
        res.headers.get('X-RateLimit-Used') != None else 0)
    if res.status_code == 403:
        clear_footer()
        print_footer()
        return handle_rate_limit_error(res)
    else:
        if res.status_code != 200:
            handle_log_response(res)
        res.raise_for_status()
        return res

def handle_rate_limit_error(res):
    t = res.headers.get('X-RateLimit-Reset')
    if t is not None:
        t = max(0, int(int(t) - time.time()))
    else: 
        t = int(res.headers.get('Retry-After', 60))
    err_msg = f'Exceeded rate limit. Retrying after {t} seconds...'
    if not args.github_token:
        err_msg += ' Try running the script with a GitHub TOKEN.'
    old_msg = update_status(err_msg)
    time.sleep(t)
    update_status(old_msg)
    return get(res.url)

# In order to reduce the amount of GitHub API calls further we use the raw content API
# from GitHub to request the content of the single commits. This also reduces the need
# to throttle and hence makes the script theoretically faster. We define a function that
# helps to request data from the 'raw.githubusercontent.com/' API.

def get_content(url):
    try:
        res = requests.get(url)
    except requests.ConnectionError:
        print("\nERROR :: There seems to be a problem with your internet connection.")
        return signal_handler(0,0)
    if res.status_code != 200:
        handle_log_response(res)
    res.raise_for_status()
    return res

# This helper function can be used to write information on the Response from a request 
# into a log-file (default: log.txt).

def handle_log_response(res,file="log.txt"):
    err_msg = f'Request response error with status: {res.status_code} (for details see {file})'
    old_msg = update_status(err_msg)
    logger = open(file, "a")
    logging_str =  "\n\nTime: " + time.strftime("%m/%d/%Y, %H:%M:%S", time.localtime()) 
    logging_str += "\nRequest: " + str(res.url) + "\nStatus: "+ str(res.status_code)
    logging_str += "\nMessage: " + res.json()['message'] if res.status_code != 200 else ''
    logger.write(logging_str)
    logger.close()
    time.sleep(1.5)
    update_status(old_msg)

# We also define a convenient function to do the code search for a specific
# stratum. Note that we sort the search results by how recently a file has been
# updated (sort can be one of: stars, forks, help-wanted-issues, updated).
# We append search criteria 'fork' and 'license' depending on the user input 
# to refine the search results.

def search(a,b,order='asc',license="no"):
    q_fork = 'true' if args.forks else 'false'
    q_license = f'license:{license}' if license != "no" else ''
    query = f'language:Solidity size:{a}..{b} fork:{q_fork} {q_license}'
    
    return get('https://api.github.com/search/repositories',
        params={'q': query, 'sort': 'updated', 'order': order, 'per_page': 100})


# A function that downloads a GitHub repository into a specified directory.

def download_github_repository(github_url, repo_dirc):
    try:
        # Clone the GitHub repository
        repo = git.Repo.clone_from(github_url, repo_dirc)
        update_status(f"Repository '{repo.remote().url}' successfully cloned into '{repo_dirc}'.")
        return repo
    except git.exc.GitCommandError as e:
        update_status(f"Failed to clone repository: {str(e)}")
        return False
    

# With this helper function we can check if a repository has a license that we can use.
# We call the GitHub api for each repository and check if the license exists and if it
# is in the list of hardcoded open source licenses.
# This step is only necessary if the argument --check-repo-license is set to True.
# We do not use the '/license' endpoint of GitHub because it returns a status 404 if the 
# repository does not have a license.

def check_license(repo_full_name):
    licenses = ['apache-2.0', 'agpl-3.0', 'bsd-2-clause', 'bsd-3-clause', 'bsl-1.0',
            'cc0-1.0', 'epl-2.0', 'gpl-2.0', 'gpl-3.0', 'lgpl-2.1', 'mit',
            'mpl-2.0', 'unlicense']
    res = get('https://api.github.com/repos/' + str(repo_full_name) + '')
    if res.json()['license'] and res.json()['license']['key'] and res.json()['license']['key'] in licenses:
        # global license
        # license = res.json()['license']['key']
        return True
    else:
        return False
        

#-------------------------------------------------------------------------------

# To download all repos/files/commits returned by a code search (up to the limit 
# of 1000 repo search results imposed by GitHub), we need to deal with pagination.
# On each page, we loop through all items and add them and their metadata to our
# results database (which will be set up in the next section), provided they're 
# not already in the database (which can happen when continuing a previous search).
# We filter the files in each repository and store only the desired one. We then
# get the entire history of commits for each file, loop through all items using
# pagination and store the commits in the results database.
# Also, if any of the repos or files or commits can not be downloaded, for whatever
# reason, they are simply skipped over and count as not sampled.

# DOWNLOAD REPOS
# First we define a function to download all repos from a page of search results.

def download_all_repos(res):
    download_repos_from_page(res)
    while 'next' in res.links:
        update_status('Getting next page of search results...')
        global pop_repo
        res = get(res.links['next']['url'])
        pop2 = res.json()['total_count'] + current_cumulative_pop
        pop_repo = max(pop_repo,pop2)
        download_repos_from_page(res)
        if sam_repo >= pop_repo:
            break
    update_status('')

# We then loop over all repos on one page and do the following for each repo:
# The repo is cloned into the 'repo' directory. Then we get a list of all Solidity
# files in the repo and loop over them. For each file we get the commit history and
# store it in the results database. If the repo is already in the database, we skip
# it. If the repo has no license, we skip it. If the repo has only one commit, we skip
# it. After handling all files in the repo, we delete the 'repo' directory to make
# space for the next repo.

def download_repos_from_page(res):
    for repo in res.json()['items']:
        update_status(f"Working on repository '{repo['full_name']}'...")
        global current_repo
        current_repo = repo['full_name']
        clear_footer()
        print_footer()

        # Delete everything in the ./repo directory
        subdirectory_path = "./repo"
        try:
            # List all files and subdirectories in the subdirectory
            subdirectory_contents = os.listdir(subdirectory_path)

            # Iterate through the contents and remove each item
            for item in subdirectory_contents:
                item_path = os.path.join(subdirectory_path, item)
                if os.path.isfile(item_path):
                    os.unlink(item_path)
                elif os.path.isdir(item_path):
                    shutil.rmtree(item_path)
            update_status(f"Successfully cleared contents of {subdirectory_path}.")
        except OSError as e:
            sys.exit(f"Error clearing contents of {subdirectory_path}: {e}")

        # Double Check if the repository has a license
        if args.licensed:
            if not check_license(repo['full_name']):
                update_status(f"Repository '{repo['full_name']}' has no license. Skipping processing.")
                continue

        # Check if the repository is already in the database
        if mongo_collection.find_one({"repo.repo_id": repo['id']}) != None:
            update_status(f"Repository '{repo['full_name']}' already in database. Skipping processing.")
            continue
        
        # Download the repository into a folder
        try:
            gitrepo = download_github_repository("https://github.com/" + repo["full_name"], repo_directory)
        except git.exc.GitCommandError as e:
            update_status(f"Error downloading repository: {e}")
            continue

        # Check if the repository could be downloaded
        if not gitrepo:
            update_status(f"Repository '{repo['full_name']}' could not be downloaded. Skipping processing.")
            continue

        # Switch to the default branch
        default_branch = gitrepo.heads[0]
        default_branch.checkout()

        # Check if the repository has more than two commits
        if len(list(gitrepo.iter_commits())) < 2:
            update_status("Repository has only one commit. Skipping processing.")
            continue

        # Get a list of file_paths for all Solidity files in the repository
        solidity_files = gitrepo.git.ls_files("*.sol").split("\n")
        solidity_files = [file for file in solidity_files if file != '']

        # Loop over all Solidity files and build db document
        for file_path in solidity_files:
            update_status(f"Working on file '{file_path}'...")
            history = commit_history(repo_directory, file_path) #+ "/" + repo['name']
            if len(history) < 2:
                update_status("File has only one commit. Skipping processing.")
                continue
            fname = file_path.split("/")[-1][:-4]
            document = {
                # "_id": { "$oid": "63f64e1cd56ad6d1d7c1a887" },
                "name": fname,
                "path": file_path,
                "sha": gitrepo.git.rev_parse(f"HEAD:{file_path}").strip(),
                "language": "Solidity",
                "license": current_license,
                "repo": {
                    "repo_id": repo['id'],
                    "full_name": repo['full_name'],
                    "description": repo['description'],
                    "url": repo['url'],
                    "owner_id": repo['owner']['id']
                },
                "versions": history
            }

            insert_document(document, repo['id'])

        global sam_repo, total_sam_repo
        sam_repo += 1
        total_sam_repo += 1
        clear_footer()
        print_stratum(overwrite=True)
        print_footer()
        if sam_repo >= pop_repo:
            return

# Function to flatten a Solidity file using a npm flattener tool.
# The flattener tool comes from https://github.com/poanetwork/solidity-flattener.
# It stores the flattened content in a file in ./solidity-flattener/out.

def flatten_solidity_file(input_path):
    try:
        result = subprocess.run(["npm", "start", input_path], cwd="./solidity-flattener",
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        update_status(f"Error flattening Solidity file {input_path}: {e}")
        return None


# Function to get the commit history for a Solidity file
# The commit history is stored in a list of dictionaries
# For each commit we store the commit sha, the author, the date, the message, the parents
# and the flattened content of the file at that commit.

def commit_history(repo_path, file_path):
    repo = git.Repo(repo_path)
    commit_history = []

    # Get the currently active branch (should be the default branch)
    default_branch = repo.active_branch

    vid = 0
    for commit in repo.iter_commits(paths=file_path):
        # update_status(f"Working on commit {commit.hexsha}")
        commit_info = {
            "version_id": vid,
            "commit_sha": commit.hexsha,
            "author": f"{commit.author.name} <{commit.author.email}>",
            "date": commit.authored_datetime,
            "message": commit.message.strip(),
            "parents": [parent.hexsha for parent in commit.parents],
            "compiler_version": None,  # To store the compiler version
            "content": None  # To store the flattened content
        }

        # Checkout the commit to access the file at that specific commit
        repo.git.checkout(commit)

        # Attempt to flatten the file
        flattener_output = flatten_solidity_file("../" + repo_path + "/" + file_path)

        if flattener_output is not None:
            # flattened_content is stored in ./solidity-flattener/out/
            # The file name will be the original file name with _flat appended
            flattened_file_name = file_path.split("/")[-1][:-4] + "_flat" + ".sol"
            flattened_file_path = "./solidity-flattener/out/" + flattened_file_name

            # Read the flattened content from the file
            with open(flattened_file_path, "r") as flattened_file:
                flattened_content = flattened_file.read()
                commit_info["content"] = flattened_content
            
            compiler_version = find_compiler_version(flattened_content)
            commit_info["compiler_version"] = compiler_version

            # Append commit to the history        
            commit_history.append(commit_info)
            vid += 1
            global sam_comit, total_sam_comit
            sam_comit += 1
            total_sam_comit += 1

        # After appending the commit, the out/ directory is cleared
        # This is done to avoid any conflicts with the next commit that will be checked out
        try:
            # List all files and subdirectories in the subdirectory
            subdirectory_contents = os.listdir("./solidity-flattener/out")
            # Iterate through the contents and remove each item
            for item in subdirectory_contents:
                item_path = os.path.join("./solidity-flattener/out", item)
                if os.path.isfile(item_path):
                    os.unlink(item_path)
                elif os.path.isdir(item_path):
                    shutil.rmtree(item_path)
        except OSError as e:
            update_status(f"Error clearing contents of out/ directory: {e}")
    
    # Check out the default branch and set back to HEAD
    default_branch.checkout()

    return commit_history

#-------------------------------------------------------------------------------
# This function helps to insert a document into the mongodb collection.
# It checks if the document is already in the database and if not inserts it.
# If the document is already in the database it prints an error message.

def insert_document(document, repo_id):
    # Check for duplicate documents in the database
    # Usually the file sha uniquely identifies the file. However, if forks are included in 
    # the database, the same file sha can exist for different files. Therefore uniqueness
    # can only be guaranteed if the repo_id and the file sha are combined.
    duplicate = mongo_collection.find_one({"repo.repo_id": repo_id, "sha": document['sha']})
    if duplicate != None:
        update_status('File "%s" already exists in MongoDB' % document['path'])
        return
    
    # Insert into mongodb collection
    inserted = mongo_collection.insert_one(document).inserted_id
    if not inserted:
        update_status('ERROR :: Inserting "%s" into MongoDB failed' % document['path'])
        return
    global sam_file, total_sam_file
    sam_file += 1
    total_sam_file += 1
    return inserted

# For convenience, we define a short function that uses a regex to get the 
# compiler version of a Solidity file.

def find_compiler_version(text):
    compiler_vers = ""
    compiler_re = re.search(r'pragma solidity [<>^]?=?\s*([\d.]+)', text)
    if compiler_re != None:
        compiler_vers = compiler_re.group(1)
    return compiler_vers

#-------------------------------------------------------------------------------

# Now we can finally get into it! 

status_msg = 'Initialize Program'
print_footer()
total_sam_repo = 0
total_sam_file = 0
total_sam_comit = 0

# Before starting the iterative search process, let's see if we have a sampling
# statistics file that we could use to continue a previous search. If so, let's
# get our data structures and UI up-to-date; otherwise, create a new statistics
# file.

if os.path.isfile(args.statistics):
    update_status('Continuing previous search...')
    with open(args.statistics, 'r') as f:
        fr = csv.reader(f)
        next(fr) # skip header
        for row in fr:
            strat_first = int(row[0])
            strat_last = int(row[1])
            pop_repo = int(row[2])
            sam_repo = int(row[3])
            sam_file = int(row[4])
            sam_comit = int(row[5])
            total_sam_repo += sam_repo
            total_sam_file += sam_file
            total_sam_comit += sam_comit
            clear_footer()
            print_stratum()
            print_footer()
        if pop_repo > -1:
            strat_first += args.stratum_size
            strat_last = min(strat_last + args.stratum_size, args.max_size)
            pop_repo = -1
            sam_repo = -1
            sam_file = -1
            sam_comit = -1
else:
    with open(args.statistics, 'w') as f:
        f.write('stratum_first,stratum_last,population_repo,sample_repo,sample_file,sample_comit\n')

statsfile = open(args.statistics, 'a', newline='')
stats = csv.writer(statsfile)

#-------------------------------------------------------------------------------

# Let's also quickly define a signal handler to cleanly deal with Ctrl-C. If the
# user quits the program and cancels the search, we want to allow him to later
# continue more-or-less where he left of. So we need to properly close the
# database and statistic file.

def signal_handler(sig,frame):
    mongo_client.close()
    statsfile.flush()
    statsfile.close()
    print("\nThe program took " + time.strftime("%H:%M:%S", 
        time.gmtime((time.time())-start)) + " to execute (Hours:Minutes:Seconds).")
    print("The program has requested " + str(api_calls) + " API calls from GitHub.")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

#-------------------------------------------------------------------------------

clear_footer()
print_stratum()
print_footer()

# Iterating through all the strata, we want to sample as much as we can.

while strat_first <= args.max_size:

    pop_repo = 0
    sam_repo = 0
    sam_file = 0
    sam_comit = 0

    # We check whether the search should filter for a license or not.

    if not args.licensed:

        update_status('Searching...')
        res = search(strat_first, strat_last)
        pop_repo = int(res.json()['total_count'])
        clear_footer()
        print_stratum(overwrite=True)
        print_footer()

        download_all_repos(res)

        # To stretch the 1000-results-per-query limit, we can simply repeat the
        # search with the sort order reversed, thus sampling the stratum population
        # from both ends, so to speak. This gives us a maximum sample size of 2000
        # per stratum.

        if pop_repo > 1000:
            update_status('Repeating search with reverse sort order...')
            res = search(strat_first, strat_last, order='desc')
            
            # Due to the instability of search results, we might get a different
            # population count on the second query. We will take the maximum of the
            # two population counts for this stratum as a conservative estimate.

            pop2 = int(res.json()['total_count'])
            pop_repo = max(pop_repo,pop2)
            clear_footer()
            print_stratum(overwrite=True)
            print_footer()

            download_all_repos(res)


    else:
        
        # Within the strata we loop through the list of licenses and search for
        # files with the 'license' filter.

        for lic in licenses:
            update_status(f'Searching for >>{lic}<< licensed repositories...')
            current_license = lic
            res = search(strat_first, strat_last,license=lic)
            current_cumulative_pop = pop_repo
            pop_repo += int(res.json()['total_count'])
            clear_footer()
            print_stratum(overwrite=True)
            print_footer()

            download_all_repos(res)

            if pop_repo > 1000:
                update_status('Repeating search with reverse sort order...')
                res = search(strat_first, strat_last, order='desc',license=lic)

                pop2 = int(res.json()['total_count']) + current_cumulative_pop
                pop_repo = max(pop_repo,pop2)
                clear_footer()
                print_stratum(overwrite=True)
                print_footer()

                download_all_repos(res)


    # After we've sampled as much as we could of the current strata, commit it
    # to the table and move on to the next one.

    stats.writerow([strat_first,strat_last,pop_repo,sam_repo,sam_file,sam_comit])
    statsfile.flush()
    
    strat_first += args.stratum_size
    strat_last = min(strat_last + args.stratum_size, args.max_size)
    pop_repo = -1
    sam_repo = -1
    sam_file = -1
    sam_comit = -1

    clear_footer()
    print_stratum()
    print_footer()

mongo_client.close()
update_status('Done.')
print("The program took " + time.strftime("%H:%M:%S", time.gmtime((time.time())-start)) + 
    " to execute (Hours:Minutes:Seconds).")
print("The program has requested " + str(api_calls) + " API calls from GitHub.\n\n")

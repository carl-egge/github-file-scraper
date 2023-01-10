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

######## VERSION: Search for User-specified language
#    ----> Search Logic: GitHub Repository Search Endpoint

# This script is a modification of the github-searcher from Michael Schröder and
# Jürgen Cito. It exhaustively samples GitHub Repo Search results and stores the 
# files of a specified language including their commit history and their content.

# This script was developed by Carl Egge on behalf of the Christian Doppler Labor.
# Its main purpose is to build a local database of Solidity smart contracts and
# their versions. It is build in a semi-chronical readable fashion.

import os, sys, argparse, shutil, time, signal, re
import sqlite3, csv
import requests

# Before we get to the fun stuff, we need to parse and validate arguments, check
# environment variables, set up the help text and so on.

# fix for argparse: ensure terminal width is determined correctly
os.environ['COLUMNS'] = str(shutil.get_terminal_size().columns)

parser = argparse.ArgumentParser(
    formatter_class=argparse.RawDescriptionHelpFormatter,
    description='''Exhaustively sample the GitHub Code Search API and 
        store files and commits of a given programming language.''')

parser.add_argument('-l', '--language', metavar='LANGUAGE',
    default='Solidity',
    help='''Please provide the name of the programming language that you
        want to search for (default: Solidity)''')

parser.add_argument('-e', '--extension', metavar='EXTENSION',
    default='sol',
    help='''Please provide the file extension corresponding to the programming
        language that should be sampled without the dot (default: sol)''')

parser.add_argument('--database', metavar='FILE', default='results.db', 
    help='search results database file (default: results.db)')

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

# TODO Rename to license-filter
parser.add_argument('--open-license', dest='licensed', action='store_true', 
    help='only search for code with open source licenses')

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
    sys.exit('missing environment variable GITHUB_TOKEN')

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

# ...as well as the current stratum's population and the amount of files sampled
# so far (in the current stratum). A value of -1 indicates "unknown".

# TODO: Optionally a global counter for the current sample of commits could
# be introduced

pop = -1
sam = -1

# We also have an estimate of the overall population (although it's gonna be
# very unreliable), and we keep track of the total (cumulative) sample size so
# far, and we store the (cumulative) amount of downloaded commits.

# est_pop = -1
total_sam = -1
total_com = 0

# We also want to keep track of the execution time of the script, therefore we 
# store the starting time. Additionally we store the ratelimit-used information
# to keep track of how many api_calls can still use. And just for information 
# we count the total amount of github api calls that have been made.

start = time.time()
rate_used = 0
api_calls = 0

# Store list of opensource liscense keys for GitHub API. This list only includes
# copyleft licenses that have no condition or only the condition to include a
# open source license again ('include-copyright') when redistributing the work.
licenses = ['mit', 'unlicense', 'cc0-1.0', 'bsd-2-clause', 'bsd-3-clause']
current_lics = ''

# The secondary rate limit blocks heavy requests when they come too quickly one
# after the other. Therefore we store and check the last time we ran a search.
last_search = 0

#-------------------------------------------------------------------------------

# During the search we want to display a table of all the strata sampled so far,
# plus the stratum currently being sampled, some summary information, and a
# status message. These last three items will be continuously updated to signal
# the progress that's being made.

# First, let's just print the table header.

print('                 ┌────────────┬────────────┐')
print('                 │ population │   sample   │')
print('                 ├────────────┼────────────┤')

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
    pop_str = str(pop) if pop > -1 else ''
    sam_str = str(sam) if sam > -1 else ''
    per = '%6.2f%%' % (sam/pop*100) if pop > 0 else ''
    print('%16s │ %10s │ %10s │ %6s' % (size, pop_str, sam_str, per))

# Another function will print the footer of the table, including summary
# statistics and the status message. Here we provide a separate function to
# clear the footer again.

status_msg = ''

def print_footer():
    if args.min_size == args.max_size:
        size = '%d' % args.min_size
    else:
        size = '%d .. %d' % (args.min_size, args.max_size)
    # pop_str = str(est_pop) if est_pop > -1 else ''
    sam_str = str(total_sam) if total_sam > -1 else ''
    # per = '%6.2f%%' % (total_sam/est_pop*100) if est_pop > 0 else ''
    print('                 ├────────────┼────────────┤')
    print('                 │ population │   sample   │')
    print('                 └────────────┴────────────┘')
    print('%16s   %10s   %10s   %6s' % (size, '', sam_str, ''))
    print() # print('                   (estimated)') if est_pop > -1 else print()
    print('Current license: ', current_lics)
    print('Total of downloaded commits: ', str(total_com))
    print('Current GitHub Ratelimit: %d / ~5000' % (rate_used))
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
        time.sleep(0.4) # throttle requests to ~5000 per hour: 0.72
    try:
        res = requests.get(url, params, headers=
            {'Authorization': f'token {args.github_token}'})
    except:
        print("\nERROR :: There seems to be a problem with your internet connection.")
        return signal_handler(0,0)
    api_calls += 1
    rate_used = (int(res.headers.get('X-RateLimit-Used')) if
        res.headers.get('X-RateLimit-Used') != None else 0)
    if res.status_code == 403:
        return handle_rate_limit_error(res)
    else:
        # TODO: Add logging of error message
        res.raise_for_status()
        return res

def handle_rate_limit_error(res):
    t = res.headers.get('X-RateLimit-Reset')
    if t is not None:
        t = max(0, int(int(t) - time.time()))
    else: 
        t = int(res.headers.get('Retry-After', 60))
    
    # TODO: Is this buffer needed?
    # l = res.res.headers.get('X-RateLimit-Limit')
    # if (l is not None and l == 30):
    #     # Secondary Rate Limit Error so we increase the buffing time
    #     t += 60

    err_msg = f'Exceeded rate limit. Retrying after {t} seconds...'
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
    except:
        print("\nERROR :: There seems to be a problem with your internet connection.")
        return signal_handler(0,0)
    res.raise_for_status()
    return res

# This little helper function can be used to write information on the Response from any
# request that has been executed into a log-file (default: log.txt).

def handle_log_response(res,file="log.txt"):
    logger = open(file, "a")
    logging_str =  "\n\nTime: " + time.strftime("%m/%d/%Y, %H:%M:%S", time.localtime()) 
    logging_str += "\nRequest: " + str(res.url) + "\nStatus: "+ str(res.status_code)
    logging_str += "\nMessage: " + res.json()['message'] if res.json()['message'] else ''
    logger.write(logging_str)
    logger.close()

# We also define a convenient function to do the code search for a specific
# stratum. Note that we sort the search results by how recently a file has been
# updated (sort can be one of: stars, forks, help-wanted-issues, updated).
# We append search criteria 'fork' and 'license' depending on the user input 
# to refine the search results.

def search(a,b,order='asc',license="no"):
    global last_search
    last_search = time.time()

    q_fork = 'true' if args.forks else 'false'
    q_license = f'license:{license}' if license != "no" else ''
    query = f'language:{args.language} size:{a}..{b} fork:{q_fork} {q_license}'
    
    return get('https://api.github.com/search/repositories',
        params={'q': query, 'sort': 'updated', 'order': order, 'per_page': 100})

#-------------------------------------------------------------------------------

# To download all repos/files/commits returned by a code search (up to the limit 
# of 1000 imposed by GitHub), we need to deal with pagination. On each page, we
# loop through all items and add them and their metadata to our results database 
# (which will be set up in the next section), provided they're not already in 
# the database (which can happen when continuing a previous search). 
# 
# Also, if any of the repos or files or commits can not be downloaded, for whatever
# reason, they are simply skipped over and count as not sampled.

# DOWNLOAD REPOS
# For each repository we request a list of files from the master branch and filter 
# this list for required files.

def download_all_repos(res):
    download_repos_from_page(res)
    while 'next' in res.links:
        update_status('Getting next page of search results...')
        global pop
        res = get(res.links['next']['url'])
        pop2 = res.json()['total_count']
        pop = max(pop,pop2)
        download_repos_from_page(res)
        if sam >= pop:
            break
    update_status('')

def download_repos_from_page(res):
    global sam, total_sam
    update_status('Get list of files in repository...')
    for item in res.json()['items']:
        # TODO: Remove this try and fix function and signal handler
        try: 
            if known_repo(item):
                continue
            else:
                insert_repo(item)
                try:
                    # Request list of files in repository
                    # Note: The limit for the tree array is 100,000 entries with a 
                    # maximum size of 7 MB when using the recursive parameter.
                    res = get("https://api.github.com/repos/" + item["full_name"] 
                            + "/git/trees/" + item["default_branch"] + "?recursive=1")
                except KeyboardInterrupt:
                    signal_handler()
                except:
                    continue
                if res.status_code != 200:
                    continue

                update_status('Store files')
                for node in res.json()['tree']:
                    if(node['type'] == "blob" and bool(re.search(fr'\w\.{args.extension}$', node['path']))):
                        # Extract the file name from the path using regex
                        name_re = re.search(r'[\w-]+?(?=\.)', node['path'])
                        node['name'] = name_re.group(0) if name_re != None else node['path']
                        if not known_file(node, item['id']):    
                            # Store the file in the database
                            file_id = insert_file(node, item['id'])
                            download_all_commits(item, node, file_id)
            sam += 1
            total_sam += 1
            clear_footer()
            print_stratum(overwrite=True)
            print_footer()
            if sam >= pop:
                return
        except KeyboardInterrupt:
            signal_handler()

# DOWNLOAD COMMITS
# For each of the files a list of commits is requested from the Github API.
# The list of commits will again be paginated (with 100 elements per page).
# Hence we loop over all pages and each of the commits on the pages. For a
# commit the file content is then downloaded from the Raw Github API that 
# has no rate limit. In the end the commits are also stored in the results
# database, if they are not already in there.

def download_all_commits(repo, file, file_id):
    # Get the list of commits for this file
    commits_url = repo['commits_url'][:-6].replace('#', '%23')
    commits_res = get(commits_url, params={'path': file['path'], 'per_page': 100})
    if commits_res.status_code != 200:
        return 
    download_commits_from_page(commits_res, repo['full_name'],
                                file['path'], file_id)
    # If more than 100 commits exist we loop over the pages of commits
    while 'next' in commits_res.links:
        update_status('Getting next page of commits...')
        commits_res = get(commits_res.links['next']['url'])
        download_commits_from_page(commits_res, repo['full_name'],
                                    file['path'], file_id)
    update_status('')

def download_commits_from_page(commits_res, repo_full_name, file_path, file_id):
    count_commits = str(len(commits_res.json())) if len(commits_res.json()) < 100 else "100+"
    update_status('Downloading ' + count_commits + ' commits...')
    for commit in commits_res.json():
        if not known_commit(commit, file_id):
            try:
                content_res = get_content("https://raw.githubusercontent.com/" +
                    repo_full_name + "/" + commit['sha'] + "/" + file_path)
            except:
                continue

            # Extract only shas of parents from api response
            parents = []
            for p in commit['parents']:
                parents.append(p['sha'])
            insert_commit(commit, content_res, parents, file_id)
            global total_com
            total_com += 1
    

#-------------------------------------------------------------------------------

# This is a good place to open the connection to the results database, or create
# one if it doesn't exist yet. The database schema follows the GitHub API
# response schema. Our 'insert_repo', 'insert_file' and 'insert_comit' functions
# help to store the items in the database respectively. 'commit' is a reserved 
# keyword in sqlite, therefore the tablename is 'comit'.

db = sqlite3.connect(args.database)
db.executescript('''
    CREATE TABLE IF NOT EXISTS repo 
    ( repo_id INTEGER PRIMARY KEY
    , name TEXT NOT NULL
    , full_name TEXT NOT NULL
    , description TEXT
    , url TEXT NOT NULL
    , fork INTEGER NOT NULL
    , owner_id INTEGER NOT NULL
    , owner_login TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS file
    ( file_id INTEGER PRIMARY KEY
    , name TEXT NOT NULL
    , path TEXT NOT NULL
    , sha TEXT NOT NULL
    , repo_id INTEGER NOT NULL
    , FOREIGN KEY (repo_id) REFERENCES repo(repo_id)
    , UNIQUE(path,repo_id)
    );
    CREATE TABLE IF NOT EXISTS comit
    ( comit_id INTEGER PRIMARY KEY
    , sha TEXT NOT NULL
    , message TEXT NOT NULL
    , size INTEGER NOT NULL
    , created DATETIME DEFAULT CURRENT_TIMESTAMP
    , content TEXT NOT NULL
    , parents TEXT NOT NULL
    , file_id INTEGER NOT NULL
    , FOREIGN KEY (file_id) REFERENCES file(file_id)
    , UNIQUE(sha,file_id)
    );
    ''')

# We also count the commits that have already been downloaded
total_com = int(db.execute("SELECT COUNT() FROM comit").fetchone()[0])

def insert_repo(repo):
    db.execute('''
        INSERT OR IGNORE INTO repo 
            ( repo_id, name, full_name, description, url, fork
            , owner_id, owner_login
            )
        VALUES (?,?,?,?,?,?,?,?)
        ''',
        ( repo['id']
        , repo['name']
        , repo['full_name']
        , repo['description']
        , repo['url']
        , int(repo['fork'])
        , repo['owner']['id']
        , repo['owner']['login']
        ))
    db.commit()

# Here we insert a file into the results database. For further computations
# we check the file_id after insertion and return it.

def insert_file(file,repo_id):
    local_cur = db.execute('''
        INSERT OR IGNORE INTO file
            (name, path, sha, repo_id)
        VALUES (?,?,?,?)
        ''',
        ( file['name']
        , file['path']
        , file['sha']
        , repo_id
        ))
    file_id = local_cur.lastrowid
    db.commit()
    return file_id

# In order to get the byte size of the file content we check the length of the
# content of the response object. The timestamp is stored as the string directly
# from the API response, since sqlite can't store time objects anyway.
# The parent field stores a list of git_shas that correspond to the parent commits.

def insert_commit(commit,content_res,parents,file_id):
    db.execute('''
        INSERT OR IGNORE INTO comit
            (sha, message, size, created, content, parents, file_id)
        VALUES (?,?,?,?,?,?,?)
        ''',
        ( commit['sha']
        , commit['commit']['message']
        , len(content_res.content)
        , commit['commit']['committer']['date']
        , content_res.text
        , str(parents)
        , file_id
        ))
    db.commit()

def known_repo(item):
    cur = db.execute("select count(*) from repo where full_name = ? and repo_id = ?",
        (item['full_name'], item['id']))
    return cur.fetchone()[0] == 1

def known_file(item, repo_id):
    cur = db.execute("select count(*) from file where path = ? and repo_id = ?",
        (item['path'], repo_id))
    return cur.fetchone()[0] == 1

def known_commit(item, file_id):
    cur = db.execute("select count(*) from comit where sha = ? and file_id = ?",
        (item['sha'], file_id))
    return cur.fetchone()[0] == 1

#-------------------------------------------------------------------------------

# Now we can finally get into it! 

# We do not get an estimation of the entire population in this version of the
# script because it wouldn't make sense.
status_msg = 'Initialize Program'
print_footer()
total_sam = 0

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
            pop = int(row[2])
            sam = int(row[3])
            total_sam += sam
            clear_footer()
            print_stratum()
            print_footer()
        if pop > -1:
            strat_first += args.stratum_size
            strat_last = min(strat_last + args.stratum_size, args.max_size)
            pop = -1
            sam = -1
else:
    with open(args.statistics, 'w') as f:
        f.write('stratum_first,stratum_last,population,sample\n')

statsfile = open(args.statistics, 'a', newline='')
stats = csv.writer(statsfile)

#-------------------------------------------------------------------------------

# Let's also quickly define a signal handler to cleanly deal with Ctrl-C. If the
# user quits the program and cancels the search, we want to allow him to later
# continue more-or-less where he left of. So we need to properly close the
# database and statistic file.

def signal_handler(sig,frame):
    db.commit()
    db.close()
    statsfile.flush()
    statsfile.close()
    global start
    global api_calls
    print("\nThe program took " + time.strftime("%H:%M:%S", 
        time.gmtime((time.time())-start)) + " to execute (Hours:Minutes:Seconds).")
    print("The program has requested " + str(api_calls) + " API calls from github.\n")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

#-------------------------------------------------------------------------------

clear_footer()
print_stratum()
print_footer()

# Iterating through all the strata, we want to sample as much as we can.

while strat_first <= args.max_size:
    
    # We wait some time before calling the search query to avoid secondary rate limit.
    # (The secondary rate limit blocks heavy search request if they come within short
    # amount of time with a forbidden response.)
    if time.time() - last_search < 60: 
        update_status('Buffering to avoid secondary rate limit error...')
        time.sleep(60)

    # We check whether the search should filter for a license or not.

    if not args.licensed:
        # non licensed search
        update_status('Searching...')
        res = search(strat_first, strat_last)
        pop = int(res.json()['total_count'])
        sam = 0
        clear_footer()
        print_stratum(overwrite=True)
        print_footer()

        download_all_repos(res)

        # To stretch the 1000-results-per-query limit, we can simply repeat the
        # search with the sort order reversed, thus sampling the stratum population
        # from both ends, so to speak. This gives us a maximum sample size of 2000
        # per stratum.

        if pop > 1000:
            update_status('Repeating search with reverse sort order...')
            res = search(strat_first, strat_last, order='desc')
            
            # Due to the instability of search results, we might get a different
            # population count on the second query. We will take the maximum of the
            # two population counts for this stratum as a conservative estimate.

            pop2 = int(res.json()['total_count'])
            pop = max(pop,pop2)
            clear_footer()
            print_stratum(overwrite=True)
            print_footer()

            download_all_repos(res)


    else:
        # only licensed search
        for lics in licenses:
            update_status(f'Searching for {lics} licensed repositories...')
            current_lics = lics
            res = search(strat_first, strat_last,license=lics)
            pop = int(res.json()['total_count'])
            sam = 0
            clear_footer()
            print_stratum(overwrite=True)
            print_footer()

            download_all_repos(res)

            # To stretch the 1000-results-per-query limit, we can simply repeat the
            # search with the sort order reversed, thus sampling the stratum population
            # from both ends, so to speak. This gives us a maximum sample size of 2000
            # per stratum.

            if pop > 1000:
                update_status('Repeating search with reverse sort order...')
                res = search(strat_first, strat_last, order='desc',license=lics)
                
                # Due to the instability of search results, we might get a different
                # population count on the second query. We will take the maximum of the
                # two population counts for this stratum as a conservative estimate.

                pop2 = int(res.json()['total_count'])
                pop = max(pop,pop2)
                clear_footer()
                print_stratum(overwrite=True)
                print_footer()

                download_all_repos(res)





    # After we've sampled as much as we could of the current strata, commit it
    # to the table and move on to the next one.

    stats.writerow([strat_first,strat_last,pop,sam])
    statsfile.flush()
    
    strat_first += args.stratum_size
    strat_last = min(strat_last + args.stratum_size, args.max_size)
    pop = -1
    sam = -1

    clear_footer()
    print_stratum()
    print_footer()

update_status('Done.')
print("The program took " + time.strftime("%H:%M:%S", time.gmtime((time.time())-start)) + " to execute (Hours:Minutes:Seconds).")
print("The program has requested " + str(api_calls) + " API calls from github.\n\n")

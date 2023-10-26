# Github Solidity Scraper (Version 2)

> A tool to collect and store Solidity files, their version history and some metadata from public GitHub repositories. This Version of the script clones repositories and and will only add source code to the dataset that could be flattened.

## Explanation of Branches/Versions of the Script

### Master (Version 0)
The **master** uses the GitHub endpoint for general code search (https://api.github.com/search/code). An arbitrary query can be given and the script stores the files from the response set and their commit history.

### Search-Licensed-Repos (Version 1a)
The **search-licensed-repos** branch has a different search logic that uses the GitHub endpoint for search repositories (https://api.github.com/search/repositories). A desired programming language and the corresponding extension must be provided and the script searches for repositories with this language. It then downloads all files from all repositories in the reponse set that are written in the specified programming language and also their entire commit history. In this version of the script the search results can be filtered for licenses.

### Search-Solidity-Repos (Version 1b)
The **search-solidity-repos** branch is specifically for creating a database of Solidity smart contracts. The search logic is the same as in the search-licensed-repos branch and again it can be filtered for licenses. The results database will contain Solidity files and their commit history.

### Version2-Solidity-Scraper (Version 2)
The **version2-solidity-scraper** branch implements again a different search logic that works by pull repositories the the local directory. The script fetches licensed repositories with Solidity code from the endpoint https://api.github.com/search/repositories and runs over the result list. Each repository is cloned in the local file system. Then using git directly the script finds that Solidity files in the cloned repository. The script stores only these Solidity files that can be flattened and that have multiple versions that can also be flattened. For the flattening currently the tool https://github.com/poanetwork/solidity-flattener is used. The resulting Solidity SCs and their source code history is stored directly in the local MongoDB collection. Before the next repository gets cloned into the file system old one gets deleted.

## Key features

- This script uses the [GitHub REST API](https://docs.github.com/en/rest) to collect data about Solidity repositories, files and commits.
- When the script is run, it creates a local database with information about Solidity files, their repositories, and their commit history.
- It uses the GitHub Search API repositories endpoint (https://api.github.com/search/repositories)
- In order to expand the results it uses a technique called _stratified search_
- Request throttling is used to make optimal use of the limited API
- The search results can be filtered according to various criteria
  - If specified the script will only include data that falls under open source licenses in order to avoid copyright issues
  - You can also decide whether or not to include forks in the search
- The script is built using [Python](https://docs.python.org/3/) and the _requests_ and _gitpython_ package.

**Script Steps**

1. Stratified Search on GitHub Search API
2. For each repository clone it into file system
3. For each file collect commit history
4. For each commit try to flatten file using the repo
5. Build document with flattened commit history of source code
6. Add document to local MongoDB instance

## How To Use

**Getting Started:**
To clone and run this script, you will need [Python](https://www.python.org/downloads/) (version >= 3) and [Pip](https://pip.pypa.io/en/stable/) installed on your computer.
From your command line:

```bash
# Clone this repository
$ git clone https://github.com/carl-egge/github-file-scraper.git

# Go into the repository
$ cd github-file-scraper

# Switch to branch
$ git switch version2-solidity-scraper

# Install dependencies
$ python3 -m pip install -r requirements.txt

# Run the app (optionally use arguments)
$ python3 github-scraper.py [--github-token TOKEN]
```

<br>

**Usage:**
To customize the script manually you can use arguments and control the behavior. It is strongly recommended to state a GitHub access token using the `github-token` argument.
<br>

- `--database` : Specify the connection string of the MongoDB instance that the results will be stored in (default: mongodb://localhost:27017/)
- `--statistics` : Specify a name for a spreadsheet file that is used to store the sampling statistics. This file can be used to continue a previous search if the script get interrupted or throws an exception (default: sampling.csv)
- `--stratum-size` : This is the length of the size ranges into which the search population is partitioned (default: 5)
- `--min-size` : The minimum code size that is searched for (default: 1)
- `--max-size` : The maximum code size that is searched for (default: 393216)
- `--no-throttle` : Disable the request throttling
- `--license-filter` : When enabled the script filters the search only for repositories that fall under one of githubs [licenses](https://api.github.com/licenses)
- `--search-forks` : When enabled the search includes forks of repositories.
- `--github-token` : With this argument you should specify a personal access token for GitHub (by default, the environment variable GITHUB_TOKEN is used)

<br>

> **Note:**
> The GitHub API provides a limit of 60 requests per hour. If a personal access token is provided, this limit can be extended up to 5000 requests per hour. Therefore, **you should definitely specify an access token** or have it stored in the shell environment so that the script can run efficiently.
> More information on how to generate a personal access token can be found [here](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/creating-a-personal-access-token#creating-a-personal-access-token-classic).

## Smart Contract Repository

A large dataset of Solidity Smart Contracts that has been collected using this scraper tool can be found under https://scr.ide.tuhh.de/. Please check this site to find out more about the result set and the ouput format of the script.

## License

The MIT License (MIT). Please have a look at the [LICENSE](LICENSE) for more details.

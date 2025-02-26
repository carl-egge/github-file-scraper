# Github File Scraper

> A tool to query source code files, their version history and some metadata from public GitHub repositories into a local database. Based on the github-searcher tool from ipa-lab.

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

- This script uses the [GitHub REST API](https://docs.github.com/en/rest) to collect data about repositories, files and commits.
- When the script is run, it creates a local database with information about source code files, their repositories, and their commit history.
- It uses the GitHub Search code API endpoint (https://api.github.com/search/code)
- In order to expand the results it uses a technique called _stratified search_
- Request throttling is used to make optimal use of the limited API
- The search results can be filtered according to various criteria
  - You can also decide whether or not to include forks in the search
- The script is built using [Python](https://docs.python.org/3/) and the [requests](https://pypi.org/project/requests/) package

**Script Steps**

1. Stratified Search on GitHub Search API
2. For each repository collect files
3. For each file collect commit history
4. For each commit get content
5. Store in local sqlite database

## How To Use

**Getting Started:**
To clone and run this script, you will need [Python](https://www.python.org/downloads/) (version >= 3) and [Pip](https://pip.pypa.io/en/stable/) installed on your computer.
From your command line:

```bash
# Clone this repository
$ git clone https://github.com/carl-egge/github-file-scraper.git

# Go into the repository
$ cd github-file-scraper

# Install dependencies
$ python3 -m pip install -r requirements.txt

# Run the app with a query (optionally use arguments)
$ python3 github-scraper.py 'search query' [--github-token TOKEN]
```

<br>

**Usage:**
To customize the script manually you can use arguments and control the behavior. It is strongly recommended to state a GitHub access token using the `github-token` argument.
<br>

- `QUERY` : Within quotes specify the code query that you want to search for
- `--database` : Specify the name of the database file that the results will be stored in (default: results.db)
- `--statistics` : Specify a name for a spreadsheet file that is used to store the sampling statistics. This file can be used to continue a previous search if the script get interrupted or throws an exception (default: sampling.csv)
- `--stratum-size` : This is the length of the size ranges into which the search population is partitioned (default: 1)
- `--min-size` : The minimum code size that is searched for (default: 1)
- `--max-size` : The maximum code size that is searched for (default: 393216)
- `--no-throttle` : Disable the request throttling
- `--search-forks` : When enabled the search includes forks of repositories.
- `--github-token` : With this argument you should specify a personal access token for GitHub (by default, the environment variable GITHUB_TOKEN is used)

<br>

> **Note:**
> The GitHub API provides a limit of 60 requests per hour. If a personal access token is provided, this limit can be extended up to 5000 requests per hour. Therefore, **you should definitely specify an access token** or have it stored in the shell environment so that the script can run efficiently.
> More information on how to generate a personal access token can be found [here](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/creating-a-personal-access-token#creating-a-personal-access-token-classic).

## Showcase Smart Contract Repository

**The results.db:**
The output of the script will be a [SQLite](https://www.sqlite.org/index.html) database that consits of three tables: repo, file and comit. These tables store the information that the script collects.

- _repo:_ This table holds data about the repositories that were found (e.g. `url`, `path`, `owner` ...)
- _file:_ This table contains data about the Solidity files that were found (e.g. `path`, `sha` ...)
  - The `repo_id` is a foreign key and is associated to the repo that the file was found in.
- _comit:_ The commits correspond to a file and are stored together with some metadata in this table. This table also holds the actual file content from a commit. (e.g. `sha`, `message`, `content`, `file_id` ...)
  - The `file_id` is a foreign key and is associated to the file that the commit corresponds to.
  - Commit is a reserved keyword in SQLite therefore the tablename is `comit` with one `m`.

<br>

**Look At The Data:**
In order to view and analyse the data a SQLite interface is needed. If not yet installed you can use one of many free online graphical user interfaces like ...

- https://sqliteonline.com/
- https://sqliteviewer.app/

or you can download a free database interface such as ...

- [DBeaver](https://dbeaver.io/)
- [Adminer](https://www.adminer.org/).

Feel free to use any tool you want to look at the output data.

<br>

## License

The MIT License (MIT). Please have a look at the [LICENSE](LICENSE) for more details.

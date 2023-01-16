# github-file-scraper

> A tool for scraping files of a specific language and their version history from public github repositories. Based on the github-searcher tool from ipa-lab.

## Script Steps
1. Stratified Search on GitHub Search API
2. For each repository collect files
3. For each file collect commit history
4. For each commit get content
5. Store in local sqlite database

## Usage
If you have python installed you should be able to run the script from the command line with
`python3 github-scraper.py [--optional arguments]`

## Explanation of Branches
The **master** uses the GitHub endpoint for general code search (https://api.github.com/search/code). An arbitrary query can be given and the script stores the files from the response set and their commit history.

The **search-licensed-repos** branch has a different search logic that uses the GitHub endpoint for search repositories (https://api.github.com/search/repositories). A desired programming language and the corresponding extension must be provided and the script searches for repositories with this language. It then downloads all files from all repositories in the reponse set that are written in the specified programming language and also their entire commit history. In this version of the script the search results can be filtered for licenses.

The **search-solidity-repos** branch is specifically for creating a database of Solidity smart contracts. The search logic is the same as in the search-licensed-repos branch and again it can be filtered for licenses. The results database will contain Solidity files and their commit history.

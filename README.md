# github-file-scraper

> A tool for scraping files of a specific language and their version history from public github repositories. Based on the github-searcher tool.

## Steps
1. Stratified Search on GitHub Search API
2. For each repository collect files
3. For each file collect commit history
4. For each commit get content
5. Store in local sqlite database

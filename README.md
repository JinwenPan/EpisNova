# EpisNova

A lightweight, automated RSS fetcher and filter for the daily arXiv Computer Science (cs) feed. 

## Setup

This project uses [`uv`](https://github.com/astral-sh/uv) for fast dependency management.

```bash
# Install dependencies
uv sync
```

## Usage

1. **Fetch daily RSS:**
   ```bash
   uv run fetch_arxiv_rss.py
   ```
2. **Filter and generate Chinese digests:**
   ```bash
   uv run filter_papers.py
   ```

## Configuration
1. **Output Directory**: Set your desired directory in `fetch_arxiv_rss.py` and `filter_papers.py`:
   ```python
   DATA_DIR = "/path/to/your/output_dir"
   ```
2. **LLM Filtering**:
   - Write your research interests in `input.txt`.
   - Set the `GEMINI_API_KEY` environment variable.

## Automated Execution (Cron)
The scripts are designed to run automatically. 

Example `crontab` entry:
```bash
0  23 * * * cd /path/to/EpisNova && /path/to/uv run fetch_arxiv_rss.py
30 23 * * * source ~/.bashrc && cd /path/to/EpisNova && /path/to/uv run filter_papers.py
```
*(Note: Use absolute paths in cron. `source ~/.bashrc` ensures the API key is loaded.)*
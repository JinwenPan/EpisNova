# EpisNova

A lightweight, automated RSS fetcher for the daily arXiv Computer Science (cs) feed. 

## Setup

This project uses [`uv`](https://github.com/astral-sh/uv) for fast dependency management.

```bash
# Install dependencies
uv sync
```

## Usage

```bash
uv run fetch_arxiv_rss.py
```

### Configuration
Before running, set your desired output directory in `fetch_arxiv_rss.py`:
```python
OUTPUT_DIR = "/path/to/your/output_dir"
```

### Automated Execution (Cron)
The script is designed to run automatically at a regular interval. 

Example `crontab` entry to run daily at 11:00 PM (assuming server is on US Eastern Time):
```bash
0 23 * * * cd /path/to/EpisNova && /path/to/uv run fetch_arxiv_rss.py
```
*(Note: Use absolute paths for `cd`, `uv`, and your project directory in cron jobs to avoid path issues.)*
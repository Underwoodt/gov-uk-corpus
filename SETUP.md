# Setup Guide for Gov.uk Corpus

## Step 1: Deploy to Lightship VPS

### SSH into your Lightship instance:
```bash
ssh user@your-lightship-ip
```

### Clone/Copy the project:
```bash
# Option A: Git clone (if you pushed to GitHub)
git clone https://github.com/yourusername/gov-uk-corpus.git
cd gov-uk-corpus

# Option B: Direct copy
scp -r /Users/tomunderwood/AI\ Brain/gov-uk-corpus user@your-lightship-ip:~/gov-uk-corpus
cd ~/gov-uk-corpus
```

## Step 2: Install PostgreSQL

### On Ubuntu/Debian:
```bash
sudo apt update
sudo apt install postgresql postgresql-contrib

# Start PostgreSQL
sudo systemctl start postgresql
sudo systemctl enable postgresql
```

## Step 3: Create Database and User

```bash
sudo -u postgres psql

# Inside PostgreSQL shell:
CREATE DATABASE gov_uk_urls;
CREATE USER gov_uk_crawler WITH PASSWORD 'your_secure_password_here';
ALTER ROLE gov_uk_crawler SET client_encoding TO 'utf8';
ALTER ROLE gov_uk_crawler SET default_transaction_isolation TO 'read committed';
ALTER ROLE gov_uk_crawler SET default_transaction_deferrable TO on;
GRANT ALL PRIVILEGES ON DATABASE gov_uk_urls TO gov_uk_crawler;
\q
```

## Step 4: Initialize Schema

```bash
psql -U gov_uk_crawler -d gov_uk_urls -f database/schema.sql
```

## Step 5: Python Environment Setup

```bash
# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy and configure .env
cp .env.example .env
# Edit .env with your actual PostgreSQL password
nano .env
```

## Step 6: Run Discovery

```bash
# Activate virtual environment
source venv/bin/activate

# Run URL discovery (takes ~30 minutes)
python crawlers/discover_urls.py

# Check progress
tail -f logs/run_*.log
```

**Expected output:**
```
INFO - Starting URL discovery from https://www.gov.uk/sitemap.xml
INFO - Found 1000 sitemap files
INFO - Progress: 500,000 / 1,000,000 (50%) | Success: 500,000 | Failed: 0 | Rate: 300.00 items/sec
INFO - Discovery complete: 1,000,000 unique URLs found
INFO - Report saved to: reports/report_discover_urls.md
```

## Step 7: Start Enrichment (Long-Running)

```bash
# Option A: Run in background with nohup
nohup python crawlers/enrich_metadata.py > logs/enrich_$(date +%s).log 2>&1 &

# Option B: Run with screen (recommended for SSH sessions)
screen -S enrichment
python crawlers/enrich_metadata.py
# Detach with Ctrl+A, D
# Reattach later: screen -r enrichment

# Option C: Run with supervisor (daemon management - recommended for production)
```

**Monitor progress:**
```bash
# Check database status
psql -U gov_uk_crawler -d gov_uk_urls -c "SELECT status, COUNT(*) FROM gov_uk_urls GROUP BY status;"

# Watch real-time logs
tail -f logs/run_*enrich*.log

# Check crawl_runs progress
psql -U gov_uk_crawler -d gov_uk_urls -c "SELECT * FROM crawl_runs ORDER BY started_at DESC LIMIT 1 \gx"
```

## Step 8: Start Search API

```bash
# In a new screen session or background
screen -S api
source venv/bin/activate
python -m api.app
# Server runs on http://your-lightship-ip:8000
```

**Test the API:**
```bash
curl "http://localhost:8000/health"
curl "http://localhost:8000/search/stats"
curl "http://localhost:8000/docs"  # Interactive API docs
```

## Step 9: Setup Background Scheduler (Optional)

```bash
# Create a new screen session
screen -S scheduler
source venv/bin/activate
python jobs/scheduler.py

# This will:
# - Run URL discovery daily at 1am UTC
# - Run enrichment batches every 6 hours
# - Retry failed URLs weekly
# - Generate weekly reports
```

## Step 10: Production Setup with Supervisor (Optional but Recommended)

Create `/etc/supervisor/conf.d/gov-uk-corpus.conf`:

```ini
[group:gov-uk-corpus]
programs=api,scheduler,enrich

[program:api]
directory=/home/user/gov-uk-corpus
command=/home/user/gov-uk-corpus/venv/bin/python -m api.app
user=user
autostart=true
autorestart=true
redirect_stderr=true
stdout_logfile=/home/user/gov-uk-corpus/logs/api.log
environment=PATH="/home/user/gov-uk-corpus/venv/bin",PYTHONPATH="/home/user/gov-uk-corpus"

[program:scheduler]
directory=/home/user/gov-uk-corpus
command=/home/user/gov-uk-corpus/venv/bin/python jobs/scheduler.py
user=user
autostart=true
autorestart=true
redirect_stderr=true
stdout_logfile=/home/user/gov-uk-corpus/logs/scheduler.log
environment=PATH="/home/user/gov-uk-corpus/venv/bin",PYTHONPATH="/home/user/gov-uk-corpus"

[program:enrich]
directory=/home/user/gov-uk-corpus
command=/home/user/gov-uk-corpus/venv/bin/python crawlers/enrich_metadata.py
user=user
autostart=false
autorestart=false
redirect_stderr=true
stdout_logfile=/home/user/gov-uk-corpus/logs/enrich.log
environment=PATH="/home/user/gov-uk-corpus/venv/bin",PYTHONPATH="/home/user/gov-uk-corpus"
```

Then:
```bash
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl start gov-uk-corpus:*
```

## Monitoring

### Check all running processes:
```bash
screen -ls
supervisorctl status  # if using supervisor
```

### View real-time logs:
```bash
tail -f logs/*.log
```

### Monitor database:
```bash
psql -U gov_uk_crawler -d gov_uk_urls
gov_uk_urls=> SELECT status, COUNT(*) FROM gov_uk_urls GROUP BY status;
gov_uk_urls=> SELECT * FROM crawl_runs ORDER BY started_at DESC LIMIT 5;
```

### Check disk usage:
```bash
du -sh /home/user/gov-uk-corpus
du -sh /var/lib/postgresql  # PostgreSQL data
```

## Troubleshooting

### "Connection refused" on API
- Ensure PostgreSQL is running: `systemctl status postgresql`
- Check .env credentials: `cat .env`
- Verify database exists: `psql -l -U gov_uk_crawler`

### Crawlers very slow
- Check network connectivity: `curl https://www.gov.uk/sitemap.xml`
- Monitor CPU/RAM: `top`, `free -h`
- Check if rate limiting is active (should see ~2 req/sec)

### Out of disk space
- Check: `df -h`
- PostgreSQL data: `du -sh /var/lib/postgresql/`
- Application logs: `du -sh /home/user/gov-uk-corpus/`

### Want to reset everything
```bash
# Stop all processes
screen -XS api quit
screen -XS scheduler quit
screen -XS enrichment quit

# Reset database (CAUTION: Deletes all data)
dropdb -U gov_uk_crawler gov_uk_urls
createdb -U gov_uk_crawler gov_uk_urls
psql -U gov_uk_crawler -d gov_uk_urls -f database/schema.sql

# Restart from scratch
python crawlers/discover_urls.py
```

## Accessing from Remote

After deployment, your API will be accessible at:
- **API Base:** `http://your-lightship-ip:8000`
- **API Docs:** `http://your-lightship-ip:8000/docs`
- **Search:** `http://your-lightship-ip:8000/search?q=climate`

Example from your local machine:
```bash
curl "http://your-lightship-ip:8000/search?q=climate+change&limit=5"
```

## Estimated Timeline

- **URL Discovery:** ~30 minutes (1 CPU, parallel downloads)
- **Metadata Enrichment:** ~5-7 days (rate-limited to 2 req/sec)
  - Can run enrichment in batches: 1-2 batches/day without overwhelming VPS
- **Total Initial Setup:** ~1-2 weeks for complete corpus

## Costs

- VPS ($5-10/mo): Your Lightship instance
- PostgreSQL: Included
- Bandwidth: Minimal (localhost processing)
- **Total: ~$5-10/month**

---

**Questions?** Check logs/ and reports/ directories for detailed progress information.

# BMVS Appointment Availability Checker

Automatically checks the [Bupa Medical Visa Services](https://bmvs.onlineappointmentscheduling.net.au/oasis/Default.aspx) website for available appointment slots near **Darwin, Northern Territory** (with fallback to other Australian cities).

When a slot is found, it sends you an **email alert** via Gmail.

## How It Works

1. Uses **Playwright** (headless Chromium) to navigate the BMVS booking website
2. Checks for available appointment dates near Darwin, Alice Springs, and other NT locations
3. Falls back to checking major Australian cities if no NT slots are found
4. Sends an **email alert** with slot details and a direct booking link
5. Deduplicates alerts so you only get notified once per unique slot

## Setup

### 1. Gmail App Password

You need a Gmail App Password (not your regular password):

1. Go to [Google Account Security](https://myaccount.google.com/security)
2. Enable **2-Step Verification** if not already enabled
3. Go to [App Passwords](https://myaccount.google.com/apppasswords)
4. Create a new app password (name it "BMVS Checker")
5. Copy the 16-character password

### 2. GitHub Repository Secrets

Go to your GitHub repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

Add these secrets:

| Secret Name     | Value                                    |
|-----------------|------------------------------------------|
| `GMAIL_ADDRESS` | Your Gmail address (e.g. you@gmail.com)  |
| `GMAIL_APP_PASSWORD` | The 16-char app password from step 1 |
| `ALERT_RECIPIENT` | (Optional) Email to alert — defaults to GMAIL_ADDRESS |

### 3. Enable GitHub Actions

The workflow runs automatically every 5 minutes between **6:00 AM and 8:00 AM Darwin time**. You can also trigger it manually:

```bash
# Or run locally (not recommended for daily use)
python checker.py
```

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Set environment variables
export GMAIL_ADDRESS="you@gmail.com"
export GMAIL_APP_PASSWORD="your-app-password"

# Run
python checker.py
```

## Customization

Edit the top of `checker.py` to change:

- **`TARGET_LOCATIONS`** — Cities near Darwin to check (default: Darwin, Alice Springs)
- **`FALLBACK_LOCATIONS`** — Other Australian cities if NT has no slots

## Files

```
bmvs-appointment-checker/
├── checker.py          # Main checker script
├── requirements.txt    # Python dependencies
├── state/              # Runtime state (auto-created, gitignored)
│   └── last_check.json # Dedup state
└── README.md
```

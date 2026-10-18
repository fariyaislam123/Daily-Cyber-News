# Daily Cyber News

Dashboard that collects cybersecurity and tech news from global and Bangladeshi
sources (including BGD e-GOV CIRT advisories) and summarizes each item with Gemini.

## Setup
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
setx GEMINI_API_KEY "your-key"   (then reopen the terminal)

## Run
python run_all.py

uvicorn main:app --reload

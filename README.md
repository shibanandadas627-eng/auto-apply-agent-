# AutoApply Agent (web version)

A free website where anyone can find jobs and internships, see how well each fits their CV,
and get a tailored cover letter for each one. Every visitor brings their own free AI key.

## What it does
- Finds jobs from Remotive, Arbeitnow, RemoteOK, and Greenhouse / Lever company boards
- Scores each job 0-100 against the visitor's CV, with a reason
- Writes a cover letter and CV highlights using only facts from the CV
- Drafts answers to application questions
- Tracks applications and exports to CSV
- Saves nothing on the server. Visitors download a progress file to keep their work

## What it does NOT do
- It does not fill or submit forms (that needs the local version, which runs on the user's own PC)
- It does not log in to LinkedIn, Indeed or Internshala

## Test it on your own computer first
Double-click `run_local.bat` (needs Python 3.10+). Open the address it shows, usually localhost:8501.

## Deploy it free on Streamlit Community Cloud
1. Create a free account at github.com.
2. Click **New repository**, name it `autoapply-agent`, choose **Public**, click **Create repository**.
3. Click **uploading an existing file**. Drag in ALL files from this folder (app.py, jobs.py, llm.py,
   logic.py, utils.py, cv.py, matcher.py, tailor.py, requirements.txt, README.md, .gitignore) and the
   `.streamlit` folder. Click **Commit changes**.
4. Go to share.streamlit.io and sign in with GitHub.
5. Click **Create app**, then **Deploy a public app from GitHub**.
6. Pick your repository, branch `main`, main file path `app.py`.
7. Open **Advanced settings** and, if offered, choose Python 3.12.
8. Click **Deploy**. Wait a few minutes. You get a link like `yourname-autoapply.streamlit.app`.
9. Share that link. To update the app later, edit a file on GitHub and it redeploys by itself.

## Important
- Do NOT put your own API key in the app's Secrets. Every visitor uses their own key, so your
  quota is never used and no one can see your key.
- Free Streamlit apps go to sleep when nobody visits. The first visitor clicks a button to wake it.
- Free AI tiers have small limits. The app scores 15 jobs per search by default.
- Google's free API tier may use prompts to improve its products. The app tells visitors this.
- Visitors should read every cover letter before sending it.

# **🛠️ Blueprint Compiler | Wyvern Imperial Order**
<img width="1686" height="914" alt="image" src="https://github.com/user-attachments/assets/71c7834d-3b9c-417d-b00f-a8dd95104da3" />

**Internal Documentation & Developer Guide**

This repository contains the source code for the Wyvern Imperial Order's Requisition System (Blueprint Compiler). This application serves as an internal organizational tool to bridge the gap between members requiring specific items and the crafters who hold the necessary blueprints.

By decentralizing the "who can craft what" ledger, we reduce administrative overhead and streamline in-game logistics.

## **🏗️ Architecture & Technical Overview**

The application is designed to be lightweight, easy to maintain, and perfectly aligned with our existing web infrastructure.

* **Read-Only Master Data (blueprints.json):** All base game blueprint data (materials, times, sizes, manufacturers) is stored in a static JSON file. This allows our data team to easily submit pull requests to update the catalog when new game patches drop without needing to run database migrations.  
* **Dynamic Claim Ledger (SQLite):** Member claims ("I Have This") are persisted in a local SQLite database (`crafters.db`) via `Flask-SQLAlchemy`. This provides a zero-config persistence layer perfectly scaled for our current roster size.  
* **Frontend Integration:** The UI is built with vanilla HTML/JS and is styled to hook directly into Wyvern's existing design system. It inherits properties from our primary `styles.css` (utilizing standard `--app-panel`, `--app-line`, and `--app-text` CSS variables) to ensure visual consistency across org tools.  
* **Asynchronous UX:** The frontend utilizes lightweight fetch API calls for claim submissions to prevent page reloads, paired with an automated DOM-updating system and toast notifications for immediate user feedback.

## **🧰 Tech Stack**

* **Backend Core:** Python 3.8+, Flask  
* **ORM / Database:** Flask-SQLAlchemy, SQLite  
* **Frontend:** HTML5, Jinja2 Templating, Vanilla JavaScript (ES6)  
* **Styling:** CSS3 (CSS Variables, Grid, Flexbox) mapped to Wyvern standards

## **📂 Project Structure**
```
Blueprint_Compiler/  
│  
├── app.py                  # Main Flask application, routing, and API endpoints  
├── blueprints.json         # Master database of all blueprints (Update this on game patches)  
├── requirements.txt        # Python dependencies  
│  
├── static/                   
│   ├── styles.css          # Wyvern core stylesheets and fallback variables  
│   └── app.js              # External JS scripts (if separated from index.html)  
│  
└── templates/  
    └── index.html          # Main application frontend (Jinja2 Template)
```
## **🚀 Local Development Setup**

To contribute to the compiler or test data updates locally, follow these steps:

### **1. Environment Setup**

Ensure you have Python installed. We highly recommend using a virtual environment to prevent dependency conflicts with other Wyvern projects.
```
git clone https://github.com/lordryn/Blueprint_Compiler.git  
cd Blueprint_Compiler  
python -m venv venv
```
```
# Activate the virtual environment:  
# Windows: venvScriptsactivate  
# Mac/Linux: source venv/bin/activate
```
### **2. Install Dependencies**
```
pip install -r requirements.txt
```
### **3. Initialize & Run**
```
python app.py
```
The application will boot on http://127.0.0.1:5000.

*Note: On initial boot, app.py will automatically run `db.create_all()` to generate the local `crafters.db` file.*

## **🔒 Production & Deployment Notes**

Before deploying this to the live Wyvern infrastructure, ensure the following are addressed:

1. **Secret Key Management:** Ensure `app.config['SECRET_KEY']` in `app.py` is dynamically loaded from the production server's environment variables (e.g., `os.environ.get('SECRET_KEY')`). Do not hardcode production keys in the repo.  
2. **WSGI Server:** Flask's built-in server is for development only. For production deployment, bind the application using a production WSGI server like gunicorn or uWSGI.  
3. **Database Backups:** Set up a cron job on the host server to periodically back up `crafters.db` to prevent loss of the organization's crafter ledger.


*For the Wyvern Imperial Order* 🐉

# Text-to-SQL Agent 🤖

## 📌 Project Description
A Natural Language to SQL application that allows users to interact with databases using simple English questions. It uses LLMs to understand user queries, generate SQL statements, retrieve database results, and provide answers in natural language.

The project is designed with a focus on query accuracy, database safety, and efficient execution using a free and flexible technology stack.

## 🛠️ Technologies Used
- **Programming Language:** Python
- **LLM Providers:** Groq, Google Gemini, Ollama
- **Agent Framework:** LangChain, LangGraph
- **Backend API:** FastAPI, Uvicorn
- **Frontend:** Streamlit
- **Database:** SQLite, SQLAlchemy
- **Data Processing:** Pandas
- **Testing:** Pytest
- **Other Tools:** Python-dotenv, Regex, Git

## ⚙️ Process Pipeline
1. **User Input:** Accepts questions in natural language.
2. **Schema Filtering:** Identifies relevant database tables and columns.
3. **Relevance Checking:** Checks whether the question is related to the selected database.
4. **Cache Checking:** Checks whether a similar query has been processed before.
5. **Disambiguation:** Identifies unclear terms and assumptions in the question.
6. **SQL Generation:** Uses an LLM to generate the SQL query.
7. **SQL Validation:** Checks queries for unsafe operations.
8. **Query Execution:** Executes validated SQL against the database.
9. **Result Validation:** Reviews results for possible incorrect answers.
10. **Answer Generation:** Converts database results into simple natural-language answers.
11. **Cache Storage:** Stores successful queries for future use.

## 📚 What I Learned
- Building LLM-powered Text-to-SQL applications.
- Understanding LangGraph agents, nodes, and conditional workflows.
- Connecting LLMs with relational databases.
- Database schema extraction and filtering.
- SQL query generation, validation, and execution.
- Implementing query caching and error-handling mechanisms.
- Building APIs using FastAPI and interactive interfaces using Streamlit.
- Evaluating LLM-generated SQL queries using automated tests.

## 🚀 Future Improvements
- Integrate embedding-based schema search for better table and column retrieval.
- Improve SQL generation accuracy using few-shot examples.
- Add support for PostgreSQL and MySQL.
- Implement query execution timeouts and stronger database security.
- Improve handling of complex joins and ambiguous questions.
- Expand the evaluation dataset to measure accuracy across different LLMs.
- Add SQL explanation features for better user understanding.

## ▶️ How to Run the Project

**1. Clone the repository**
```bash
git clone <repository-url>
cd text2sql-free
```

**2. Create and activate a virtual environment**
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

**3. Install dependencies**
```bash
pip install -r requirements.txt
```

**4. Configure environment variables**

Copy the example environment file:
```powershell
Copy-Item env.example .env
```
Add your Gemini API key or configure another supported LLM provider in `.env`.

**5. Initialize the database**
```bash
python seed_db.py
```

**6. Run the Streamlit application**
```bash
streamlit run app.py
```

**7. Run the FastAPI backend (optional)**
```bash
python -m uvicorn api:app --port 8004 --reload
```

Access the API documentation at:
`http://localhost:8004/docs`

**8. Run tests**
```bash
python -m pytest -q
```

---

⭐ Built to explore LLM-powered database interaction, AI agents, and intelligent query automation.

"""All prompt templates live here so they're easy to version and ablate."""

SCHEMA_LINKER = """You are given a database schema and a question.
List ONLY the tables and columns needed to answer the question.
Output the relevant CREATE TABLE statements verbatim, dropping irrelevant tables.
If unsure whether a table is needed, keep it.

# Full schema
{full_schema}

# Question
{question}

# Evidence (domain knowledge)
{evidence}

# Relevant schema (CREATE TABLE statements only, no prose):"""


SQL_GENERATOR = """You are an expert at writing SQLite queries.
Write ONE SQLite SELECT query that answers the question.
Use only the tables/columns in the schema. Return ONLY the SQL, no explanation.

# Schema
{schema}

# Evidence (domain knowledge — use it, it is often essential)
{evidence}

# Question
{question}

# SQL:"""


SELF_CORRECTOR = """The SQL below failed or returned a suspicious result. Fix it.
Return ONLY the corrected SQLite SELECT query, no explanation.

# Schema
{schema}

# Evidence
{evidence}

# Question
{question}

# Previous SQL
{sql}

# What went wrong
{error}

# Corrected SQL:"""

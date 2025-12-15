import os
import json
from sqlalchemy import create_engine, inspect
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("SUPABASE_DB_URL")
if not DATABASE_URL:
    raise RuntimeError("SUPABASE_DB_URL not set")

engine = create_engine(DATABASE_URL)
inspector = inspect(engine)

schema_output = {}

for table in inspector.get_table_names(schema="public"):
    columns = inspector.get_columns(table, schema="public")

    schema_output[table] = {
        "columns": {
            col["name"]: str(col["type"]) for col in columns
        },
        "primary_key": inspector.get_pk_constraint(table, schema="public"),
        "foreign_keys": inspector.get_foreign_keys(table, schema="public"),
        "indexes": inspector.get_indexes(table, schema="public"),
    }

with open("supabase_schema.json", "w", encoding="utf-8") as f:
    json.dump(schema_output, f, indent=2)

print("✅ Schema exported to supabase_schema.json")

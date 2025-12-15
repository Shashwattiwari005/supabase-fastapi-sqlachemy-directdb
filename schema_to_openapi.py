import json

with open("supabase_schema.json", "r", encoding="utf-8") as f:
    db_schema = json.load(f)

openapi = {
    "openapi": "3.1.0",
    "info": {
        "title": "Supabase PostgreSQL AI API",
        "version": "1.0.0",
        "description": (
            "API for executing PostgreSQL queries against Supabase. "
            "Schema is auto-generated from the database. "
            "Do not fabricate data."
        ),
    },
    "servers": [
        {
            "url": "postgresql://postgres:magicslidesbyIAG@db.djgurnpwsdoqjscwqbsj.supabase.co:5432/postgres",
            "description": "Main Api server",
        }
    ],
    "paths": {
        "/sqlquery_alchemy/": {
            "get": {
                "summary": "Execute PostgreSQL query",
                "operationId": "execute_sqlalchemy_query",
                "parameters": [
                    {
                        "name": "sqlquery",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "string"},
                        "description": "PostgreSQL SQL query string",
                    },
                    {
                        "name": "api_key",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                ],
                "responses": {
                    "200": {
                        "description": "Query result",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "oneOf": [
                                        {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "additionalProperties": True,
                                            },
                                        },
                                        {
                                            "type": "object",
                                            "properties": {
                                                "status": {"type": "string"},
                                                "message": {"type": "string"},
                                            },
                                        },
                                    ]
                                }
                            }
                        },
                    }
                },
            }
        }
    },
    "components": {
        "schemas": {
            "DatabaseSchema": {
                "type": "object",
                "description": "Auto-generated Supabase public schema",
                "example": db_schema,
            }
        }
    },
}

with open("openapi.json", "w", encoding="utf-8") as f:
    json.dump(openapi, f, indent=2)

print("✅ OpenAPI schema written to openapi.json")

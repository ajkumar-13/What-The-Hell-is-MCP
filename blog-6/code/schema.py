# src/schema.py - Database Schema Introspection
# Blog 6: Secure Database Analyst Part 2
# Unchanged from Blog 5 - provides schema context to LLM

from .database import db


async def get_database_schema() -> str:
    """
    Returns a markdown-formatted string describing the database schema.
    
    This function introspects your PostgreSQL database and returns
    a human-readable (and LLM-readable) description of:
    - All tables in the public schema
    - Columns with their data types
    - Nullable columns
    - Primary keys
    
    Returns:
        Markdown-formatted schema description
    """
    sql = """
    SELECT 
        c.table_name, 
        c.column_name, 
        c.data_type,
        c.is_nullable,
        CASE 
            WHEN pk.column_name IS NOT NULL THEN 'YES' 
            ELSE 'NO' 
        END as is_primary_key
    FROM 
        information_schema.columns c
    LEFT JOIN (
        -- Subquery to find primary key columns
        SELECT 
            ku.table_name, 
            ku.column_name
        FROM 
            information_schema.table_constraints tc
        JOIN 
            information_schema.key_column_usage ku
            ON tc.constraint_name = ku.constraint_name
        WHERE 
            tc.constraint_type = 'PRIMARY KEY'
            AND tc.table_schema = 'public'
    ) pk 
        ON c.table_name = pk.table_name 
        AND c.column_name = pk.column_name
    WHERE 
        c.table_schema = 'public' 
    ORDER BY 
        c.table_name, 
        c.ordinal_position;
    """
    
    async with db.get_connection() as conn:
        rows = await conn.fetch(sql)
    
    if not rows:
        return "No tables found in the public schema."
    
    # Format as Markdown for LLM readability
    schema_output = "# Database Schema\n\n"
    schema_output += "This document describes all tables in the database.\n\n"
    
    current_table = None
    table_count = 0
    
    for row in rows:
        table = row['table_name']
        
        # New table section
        if table != current_table:
            if current_table is not None:
                schema_output += "\n"
            
            table_count += 1
            schema_output += f"## Table: `{table}`\n\n"
            schema_output += "| Column | Type | Nullable | Key |\n"
            schema_output += "|--------|------|----------|-----|\n"
            current_table = table
        
        # Column row
        nullable = "Yes" if row['is_nullable'] == 'YES' else "No"
        pk = "🔑" if row['is_primary_key'] == 'YES' else ""
        schema_output += f"| `{row['column_name']}` | {row['data_type']} | {nullable} | {pk} |\n"
    
    schema_output += f"\n---\n*{table_count} tables found*\n"
    
    return schema_output


async def get_foreign_keys() -> str:
    """
    Returns foreign key relationships between tables.
    
    Helps the LLM understand how to JOIN tables correctly.
    
    Returns:
        Markdown-formatted relationship description
    """
    sql = """
    SELECT
        tc.table_name AS from_table,
        kcu.column_name AS from_column,
        ccu.table_name AS to_table,
        ccu.column_name AS to_column
    FROM
        information_schema.table_constraints AS tc
    JOIN
        information_schema.key_column_usage AS kcu
        ON tc.constraint_name = kcu.constraint_name
    JOIN
        information_schema.constraint_column_usage AS ccu
        ON ccu.constraint_name = tc.constraint_name
    WHERE
        tc.constraint_type = 'FOREIGN KEY'
        AND tc.table_schema = 'public'
    ORDER BY
        tc.table_name;
    """
    
    async with db.get_connection() as conn:
        rows = await conn.fetch(sql)
    
    if not rows:
        return "No foreign key relationships found."
    
    output = "# Table Relationships\n\n"
    output += "| From Table | Column | → | To Table | Column |\n"
    output += "|------------|--------|---|----------|--------|\n"
    
    for row in rows:
        output += (
            f"| `{row['from_table']}` | `{row['from_column']}` | → | "
            f"`{row['to_table']}` | `{row['to_column']}` |\n"
        )
    
    return output

# src/schema.py - Database Schema Introspection
# Blog 5: Secure Database Analyst
# Fetches and formats database schema for LLM consumption

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
            ku.table_schema,
            ku.table_name, 
            ku.column_name
        FROM 
            information_schema.table_constraints tc
        JOIN 
            information_schema.key_column_usage ku
            ON tc.constraint_name = ku.constraint_name
            AND tc.table_schema = ku.table_schema
        WHERE 
            tc.constraint_type = 'PRIMARY KEY'
            AND tc.table_schema = 'public'
    ) pk 
        ON c.table_schema = pk.table_schema
        AND c.table_name = pk.table_name 
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
        nullable = "✓" if row['is_nullable'] == 'YES' else "✗"
        pk = "🔑 PK" if row['is_primary_key'] == 'YES' else ""
        
        schema_output += (
            f"| `{row['column_name']}` | "
            f"{row['data_type']} | "
            f"{nullable} | "
            f"{pk} |\n"
        )
    
    # Add summary
    schema_output += f"\n---\n\n*Total tables: {table_count}*\n"
    
    return schema_output


async def get_table_sample(table_name: str, limit: int = 3) -> str:
    """
    Get a sample of data from a specific table.
    
    Useful for helping the LLM understand what kind of data
    is stored in each table.
    
    Args:
        table_name: Name of the table to sample
        limit: Number of rows to return (default 3)
        
    Returns:
        Formatted sample data
    """
    # Note: In production, validate table_name against known tables
    # to prevent SQL injection through table names
    
    sql = f"SELECT * FROM {table_name} LIMIT {limit}"
    
    async with db.get_connection() as conn:
        rows = await conn.fetch(sql)
    
    if not rows:
        return f"Table '{table_name}' is empty."
    
    # Format as readable output
    output = f"## Sample from `{table_name}` ({limit} rows)\n\n"
    
    # Get column names from first row
    columns = list(rows[0].keys())
    output += "| " + " | ".join(columns) + " |\n"
    output += "| " + " | ".join(["---"] * len(columns)) + " |\n"
    
    for row in rows:
        values = [str(row[col])[:50] for col in columns]  # Truncate long values
        output += "| " + " | ".join(values) + " |\n"
    
    return output


async def get_foreign_keys() -> str:
    """
    Returns information about foreign key relationships.
    
    This helps the LLM understand how tables relate to each other,
    enabling it to write correct JOIN queries.
    
    Returns:
        Markdown-formatted foreign key relationships
    """
    sql = """
    SELECT
        tc.table_name AS from_table,
        kcu.column_name AS from_column,
        ccu.table_name AS to_table,
        ccu.column_name AS to_column
    FROM
        information_schema.table_constraints tc
    JOIN
        information_schema.key_column_usage kcu
        ON tc.constraint_name = kcu.constraint_name
    JOIN
        information_schema.constraint_column_usage ccu
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
    output += "| From Table | From Column | → | To Table | To Column |\n"
    output += "|------------|-------------|---|----------|----------|\n"
    
    for row in rows:
        output += (
            f"| `{row['from_table']}` | "
            f"`{row['from_column']}` | → | "
            f"`{row['to_table']}` | "
            f"`{row['to_column']}` |\n"
        )
    
    return output

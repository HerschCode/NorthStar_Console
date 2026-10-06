{# Without this override, dbt's default behavior concatenates the target schema ("dbt") with
   each model's configured custom schema ("dbt_staging"), producing "dbt_dbt_staging" --
   redundant, found by actually running the build and looking at the created schema names, not
   assumed. This uses the custom schema exactly as configured in dbt_project.yml. #}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}

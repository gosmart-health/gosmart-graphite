# DevOps Scripts

This directory contains various scripts and tools used for DevOps tasks.

> [!IMPORTANT]
> ## POSSIBLE PRODUCTION DATA OBLITERATION!
> These tools can cause complete loss of production data. Use with caution.
> ## POSSIBLE EXPOSURE OF SENSITIVE INFORMATION!
> These tools can expose sensitive information. Use with caution.
> Do not patch these tools or hard-code sensitive information into them.
> `.env` files should never be committed to version control.

## `cleanup-data.py` — Data Cleanup Tool

This script cleans up the DICOM files and clears all data nodes for both DICOM
and FHIR data so that you can run DICOM and FHIR data ingestion from scratch.

Configuration and identity data are preserved.

You will be asked to confirm the deletion while running this script.

### How to Run

```shell
uv run cleanup-data.py
```

### Updating the Tool

If you modify the database schema, for example, you added a new graph node
label, ensure that this tool is updated accordingly.

## `env-secrets-upload.py`

This tool exports your `.env` values to GitHub Actions secrets and variables.
Sensitive values are exported as secrets; the rest are exported as variables.

Before you run this, make sure that your `.env` file contains all the values
you need to export for the CI/CD pipeline tests.

Use this tool to help you bulk-build your `.env` values in the CI/CD pipeline.

### How to Run

```shell
uv run env-secrets-upload.py
```

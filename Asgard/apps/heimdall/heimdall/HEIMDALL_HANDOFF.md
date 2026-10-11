# Asgard Handoff: Heimdall ServiceNow API Integration

## Current State
Heimdall currently submits security change requests by using Playwright to drive a local Microsoft Edge browser. The user reviews the form and clicks "Submit" manually. The current architecture relies on a user's PIV sign-in and a static `heimdall.json` file for field mappings.

## The Goal
We are moving away from browser automation (Playwright/Edge) and migrating Heimdall to utilize the ServiceNow REST API using a dedicated integration user (`api.jira_integrator`).

This shifts Heimdall from the "Ideal" Playwright path to an API-first approach, removing the dependency on Edge's `RemoteDebuggingAllowed` policy and eliminating the need for the Playwright `node.exe` package entirely. 

## Tasks for the Next Agent

### 1. Remove Playwright Dependency
*   Remove the `playwright` package from `requirements.txt` and `MODULES.md`.
*   Delete or deprecate `apps/heimdall/heimdall/browser.py`, which currently handles the Edge automation.

### 2. Implement ServiceNow API Client (`api.py`)
*   Create a new module (`apps/heimdall/heimdall/api.py`) to handle the REST API interactions. 
*   **Dependency Rule:** You are not strictly limited to the Python standard library (`urllib.request`). However, if you choose a third-party package (like `requests` or `httpx`), you **must** justify why it is a better choice than the stdlib. You must also pin the package exactly in `requirements.txt` and document it in `MODULES.md` alongside its stdlib fallback, adhering to Asgard's dependency policy.
*   Integrate the dynamic field discovery logic (fetching from `sc_cat_item` and `item_option_new`).
*   Implement authentication using the `api.jira_integrator` credentials. Use Asgard's preferred secret management (Windows Credential Manager via `ctypes` calls, identical to how Odin handles Jira tokens). 

### 3. Update Heimdall `init` and `fields` commands
*   Update `heimdall.cmd init` to dynamically hit the ServiceNow API, query the `item_option_new` table for the "security change" catalog item, and auto-generate the `settings\heimdall.json` file rather than relying on a static example template.
*   Ensure `heimdall.cmd fields` pulls live schema data directly from the ServiceNow instance.

### 4. Update the `fill` command 
*   Update `heimdall.cmd fill` to formulate a JSON payload mapped from `templates.py` (`settings\heimdall-templates.json`).
*   Submit the payload via `POST /api/sn_sc/servicecatalog/items/{sys_id}/order_now` (or the `submit_producer` endpoint if converted to a Record Producer).
*   **Important Guardrail:** Heimdall must still stop and require a human to review the payload and manually confirm "Submit" to fulfill the "Keep a human on Submit" requirement for security attestations. Do not submit automatically without a final confirmation prompt.
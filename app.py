from flask import Flask, render_template, jsonify, request, session, redirect, url_for, Response
from flask_cors import CORS
import requests
import json
import os
import time
import uuid
import csv
import io
import re
import difflib
from datetime import date
from spellchecker import SpellChecker
import openpyxl
from openpyxl.styles import Font

app = Flask(__name__)
CORS(app, supports_credentials=True)  # Fix CORS issues

EXPORT_ROW_LIMIT = 3000  # change here to update the cap everywhere
app.secret_key = os.environ.get('PBI_SECRET_KEY', 'change-me-pbi-saved-filters')  # stable key so sessions survive restart

# Default configuration
DEFAULT_CONFIG = {
  'tenant_id': '65693725-b6ed-4482-838d-454f6f79eafb',
  'client_id': '8264d2da-a712-455b-bb14-6e10d4db8676',
  'client_secret': 'D128Q~XhARPUB_ddynizWtw-Mouah6GTOCkPKb-C',
  'group_id': '8578855e-dbb3-47b0-b302-437d8572a9a3',
  'report_id': 'bfecbfc9-b819-41b6-94c2-17aa94e3c7bd'
}

# Where saved filters live (JSON file next to this app)
FILTERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'saved_filters.json')

# Where the /visualise field registry lives (table/column names known to be
# valid for the current report, harvested from its own slicers).
FIELDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'field_registry.json')

# Report page titles (captured on every load, no slicer needed) - lets
# /visualise guess a page from the table name by title match, even before
# the slicer sweep (triggered automatically once per report, see
# harvestReportFields() in templates/index.html) has run.
PAGES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'page_registry.json')

# User-taught keyword -> page associations (e.g. "flight cycles"/"hours" ->
# the "Aircraft Utilisation" page). Fixes cases a generic table-name/page-
# title match can't: the data can live in a table whose name points at a
# totally different page (EMISSIONS_ANALYSIS -> "Emissions") than where the
# relevant visuals actually are ("Aircraft Utilisation").
KEYWORDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'page_keywords.json')

# Groq powers the /visualise natural-language-to-filter step. Free-tier key,
# kept in an env var (never hardcoded like DEFAULT_CONFIG above).
GROQ_API_KEY = os.environ.get('GROQ_API_KEY')
GROQ_MODEL = os.environ.get('GROQ_MODEL', 'openai/gpt-oss-20b')

# Claude is preferred over Groq when available - forced tool-use gives a
# much more reliable structured response than Groq's small free-tier model
# hoping a loose text prompt gets followed (it was prone to silently
# dropping a condition from a multi-part filter request).
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY')
CLAUDE_MODEL = os.environ.get('CLAUDE_MODEL', 'claude-sonnet-5')

# Key used to hold any pre-existing single-report saves so they aren't lost
# when we move to per-report bucketing.
LEGACY_BUCKET = '_legacy_unscoped'


def get_config():
    """Get the current configuration, either from session or default"""
    if 'config' in session:
        return session['config']
    return DEFAULT_CONFIG


def current_report_key():
    """Return the per-report bucket key for the current config.

    Saved filters are keyed by user + report so switching DEFAULT_CONFIG (or
    the in-session override) automatically gives a fresh, isolated list of
    saved sets per report.
    """
    cfg = get_config()
    return f"{cfg['group_id']}::{cfg['report_id']}"


def _load_all_filters():
    """Read the whole saved-filters store.

    Returns the new shape: {user_id: {report_key: [filter_records]}}.
    If the file is in the old shape (a list directly under the user), it is
    migrated in-memory under a LEGACY_BUCKET key so historical sets are kept
    but don't leak into any specific report's list.
    """
    if not os.path.exists(FILTERS_FILE):
        return {}
    try:
        with open(FILTERS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

    if not isinstance(data, dict):
        return {}

    migrated = False
    for user_id, value in list(data.items()):
        # Old shape: value is a flat list of saved sets.
        if isinstance(value, list):
            data[user_id] = {LEGACY_BUCKET: value}
            migrated = True
        elif not isinstance(value, dict):
            # Junk row; drop it.
            data.pop(user_id, None)
            migrated = True

    return data


def _save_all_filters(data):
    """Write the whole store back to disk atomically-ish."""
    tmp = FILTERS_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, FILTERS_FILE)


def _load_field_registry():
    """Read the whole field-registry store: {report_key: [field_records]}."""
    if not os.path.exists(FIELDS_FILE):
        return {}
    try:
        with open(FIELDS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_field_registry(data):
    """Write the whole field-registry store back to disk atomically-ish."""
    tmp = FIELDS_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, FIELDS_FILE)


def _load_page_registry():
    """Read the whole page-registry store: {report_key: [{name, displayName}]}."""
    if not os.path.exists(PAGES_FILE):
        return {}
    try:
        with open(PAGES_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_page_registry(data):
    tmp = PAGES_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, PAGES_FILE)


def _load_page_keywords():
    """Read the whole keyword-library store: {report_key: {page_name: [kw, ...]}}."""
    if not os.path.exists(KEYWORDS_FILE):
        return {}
    try:
        with open(KEYWORDS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_page_keywords(data):
    tmp = KEYWORDS_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, KEYWORDS_FILE)


def get_user_id():
    """The current user key (their name/email). None if not set."""
    return session.get('user_id')


# A single /visualise request can call get_access_token() a dozen times
# over (once per value-grounding, per candidate checked, per sibling
# search, per table-consolidation check) - each one was doing a full fresh
# Azure AD OAuth round-trip with zero caching, which alone measured ~28s
# for one multi-condition request. Cache the token until shortly before
# its real expiry instead of re-fetching it every single call.
_token_cache = {}


def get_access_token():
    """Get Azure AD access token for Power BI API (cached until near expiry)."""
    config = get_config()
    cache_key = (config['tenant_id'], config['client_id'])
    cached = _token_cache.get(cache_key)
    if cached and cached['expires_at'] > time.time() + 60:
        return cached['token']

    token_url = f"https://login.microsoftonline.com/{config['tenant_id']}/oauth2/v2.0/token"
    token_data = {
        'grant_type': 'client_credentials',
        'client_id': config['client_id'],
        'client_secret': config['client_secret'],
        'scope': 'https://analysis.windows.net/powerbi/api/.default'
    }

    response = requests.post(token_url, data=token_data)
    if response.status_code == 200:
        body = response.json()
        token = body['access_token']
        expires_in = body.get('expires_in', 3600)
        _token_cache[cache_key] = {'token': token, 'expires_at': time.time() + expires_in}
        return token
    else:
        raise Exception(f"Failed to get token: {response.text}")


def get_embed_token():
    """Get Power BI embed token and report details"""
    config = get_config()
    access_token = get_access_token()
    embed_url = f"https://api.powerbi.com/v1.0/myorg/groups/{config['group_id']}/reports/{config['report_id']}/GenerateToken"

    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json'
    }

    embed_data = {
        'accessLevel': 'View'
    }

    response = requests.post(embed_url, headers=headers, json=embed_data)

    if response.status_code == 200:
        embed_response = response.json()
        report_url = f"https://api.powerbi.com/v1.0/myorg/groups/{config['group_id']}/reports/{config['report_id']}"
        report_response = requests.get(report_url, headers=headers)

        if report_response.status_code == 200:
            report_data = report_response.json()
            return {
                'embed_token': embed_response['token'],
                'embed_url': report_data['embedUrl'],
                'report_id': config['report_id'],
                'group_id': config['group_id']
            }
        else:
            raise Exception(f"Failed to get report data: {report_response.text}")
    else:
        raise Exception(f"Failed to get embed token: {response.text}")


@app.route('/')
def index():
    """Main page with embedded Power BI report"""
    try:
        embed_data = get_embed_token()

        # First time this report is opened: pull its real table/column
        # schema (DAX INFO.VIEW, no browser walk needed) so /visualise
        # already knows the data model before anyone types anything.
        # Cheap no-op on every later load since the registry stays populated.
        report_key = current_report_key()
        if not _load_field_registry().get(report_key):
            try:
                schema_fields = _fetch_dataset_schema()
                if schema_fields:
                    _merge_fields_into_registry(report_key, schema_fields)
            except Exception:
                pass  # /visualise just falls back to "no fields synced yet"

        return render_template(
            'index.html',
            embed_token=embed_data['embed_token'],
            embed_url=embed_data['embed_url'],
            report_id=embed_data['report_id'],
            group_id=embed_data['group_id'],
            user_id=get_user_id() or ''
        )
    except Exception as e:
        return f"""
        <div style="padding: 20px; background: #f8d7da; color: #721c24; border-radius: 5px; margin: 20px;">
            <h2>Server Error</h2>
            <p><strong>Error:</strong> {str(e)}</p>
            <p><strong>Type:</strong> {type(e).__name__}</p>
            <hr>
            <p><a href="/test" style="color: #0078d4;">Try Test Endpoint</a></p>
            <p><a href="/reset" style="color: #0078d4;">Reset Configuration</a></p>
        </div>
        """


@app.route('/update_config', methods=['POST'])
def update_config():
    """Update the configuration with user-provided values"""
    try:
        config = get_config()
        if request.form.get('workspace_id'):
            config['group_id'] = request.form.get('workspace_id')
        if request.form.get('report_id'):
            config['report_id'] = request.form.get('report_id')
        session['config'] = config
        return redirect(url_for('index'))
    except Exception as e:
        return f"""
        <div style="padding: 20px; background: #f8d7da; color: #721c24; border-radius: 5px; margin: 20px;">
            <h2>Configuration Error</h2>
            <p><strong>Error:</strong> {str(e)}</p>
            <p><a href="/" style="color: #0078d4;">Back to Main Page</a></p>
            <p><a href="/reset" style="color: #0078d4;">Reset Configuration</a></p>
        </div>
        """


@app.route('/reset')
def reset_config():
    """Reset configuration to defaults"""
    if 'config' in session:
        session.pop('config')
    return redirect(url_for('index'))


@app.route('/test')
def test():
    """Test endpoint to verify token generation"""
    try:
        embed_data = get_embed_token()
        config = get_config()
        return jsonify({
            'success': True,
            'message': 'Embed token generated successfully!',
            'data': {
                'token_length': len(embed_data['embed_token']),
                'embed_url': embed_data['embed_url'],
                'report_id': embed_data['report_id'],
                'token_preview': embed_data['embed_token'][:50] + '...'
            },
            'config': {
                'tenant_id': config['tenant_id'],
                'client_id': config['client_id'],
                'group_id': config['group_id'],
                'report_id': config['report_id']
            }
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'error_type': type(e).__name__
        }), 500


@app.route('/health')
def health():
    """Health check endpoint"""
    config = get_config()
    return jsonify({
        'status': 'healthy',
        'app': 'Power BI Embedded Demo',
        'port': 8080,
        'current_config': {
            'tenant_id': config['tenant_id'],
            'group_id': config['group_id'],
            'report_id': config['report_id']
        },
        'report_bucket': current_report_key(),
        'endpoints': {
            'main': 'http://localhost:8080/',
            'test': 'http://localhost:8080/test',
            'health': 'http://localhost:8080/health',
            'reset': 'http://localhost:8080/reset'
        }
    })


# ---------------------------------------------------------------------------
# Temporary diagnostic log - lets the browser report what it actually sees
# (e.g. every visual's real `type` on a page) so a mystery like "why does
# this page have no detected slicers" can be answered with real evidence
# instead of more guessing. Type "debug page" into the Visualise box while
# on the page in question to populate this.
# ---------------------------------------------------------------------------
_debug_log = []


@app.route('/api/debug/log', methods=['POST'])
def debug_log_post():
    body = request.get_json(silent=True) or {}
    body['_logged_at'] = time.time()
    _debug_log.append(body)
    del _debug_log[:-20]  # keep only the most recent entries
    return jsonify({'success': True})


@app.route('/api/debug/log', methods=['GET'])
def debug_log_get():
    return jsonify({'log': _debug_log})


# ---------------------------------------------------------------------------
# User identity (simple name/email box, no real auth)
# ---------------------------------------------------------------------------
@app.route('/login', methods=['POST'])
def login():
    """Set the current user from a name/email box."""
    if request.is_json:
        user_id = (request.json or {}).get('user_id')
    else:
        user_id = request.form.get('user_id')
    user_id = (user_id or '').strip()
    if not user_id:
        return jsonify({'success': False, 'error': 'Name or email required'}), 400
    session['user_id'] = user_id
    if request.is_json:
        return jsonify({'success': True, 'user_id': user_id})
    return redirect(url_for('index'))


@app.route('/logout', methods=['POST', 'GET'])
def logout():
    """Clear the current user."""
    session.pop('user_id', None)
    if request.method == 'GET':
        return redirect(url_for('index'))
    return jsonify({'success': True})


@app.route('/whoami')
def whoami():
    return jsonify({'user_id': get_user_id(), 'report_bucket': current_report_key()})


# ---------------------------------------------------------------------------
# Saved filters API (keyed by user + report, stored in JSON file)
# ---------------------------------------------------------------------------
@app.route('/api/filters', methods=['GET'])
def list_filters():
    """List saved filter sets for the current user, scoped to the current report."""
    user_id = get_user_id()
    if not user_id:
        return jsonify({'success': False, 'error': 'Not logged in'}), 401
    store = _load_all_filters()
    user_buckets = store.get(user_id, {})
    sets = user_buckets.get(current_report_key(), [])
    return jsonify({
        'success': True,
        'filters': sets,
        'report_bucket': current_report_key()
    })


@app.route('/api/filters', methods=['POST'])
def save_filters():
    """Save a named set of filters for the current user under the current report.

    Expected JSON body:
      { "name": "My view",
        "filters": { "filters": [...report filters...],
                     "slicers": [...slicer states...] } }
    (A bare list is also accepted for backward compatibility.)
    """
    user_id = get_user_id()
    if not user_id:
        return jsonify({'success': False, 'error': 'Not logged in'}), 401

    body = request.get_json(silent=True) or {}
    name = (body.get('name') or '').strip()
    filters = body.get('filters')
    if not name:
        return jsonify({'success': False, 'error': 'Name required'}), 400
    if not isinstance(filters, (list, dict)):
        return jsonify({'success': False, 'error': 'filters must be a list or object'}), 400

    store = _load_all_filters()
    user_buckets = store.setdefault(user_id, {})
    report_key = current_report_key()
    sets_for_report = user_buckets.get(report_key, [])

    record = {
        'id': uuid.uuid4().hex,
        'name': name,
        'filters': filters,
        'saved_at': int(time.time()),
        'report_bucket': report_key
    }
    # Overwrite any existing set with the same name (user confirmed on the frontend).
    sets_for_report = [s for s in sets_for_report if s.get('name') != name]
    sets_for_report.append(record)
    user_buckets[report_key] = sets_for_report
    store[user_id] = user_buckets
    _save_all_filters(store)
    return jsonify({'success': True, 'filter': record, 'report_bucket': report_key})


@app.route('/api/filters/<filter_id>', methods=['DELETE'])
def delete_filter(filter_id):
    """Delete one saved filter set by id, scoped to the current user + report."""
    user_id = get_user_id()
    if not user_id:
        return jsonify({'success': False, 'error': 'Not logged in'}), 401
    store = _load_all_filters()
    user_buckets = store.get(user_id, {})
    report_key = current_report_key()
    sets_for_report = user_buckets.get(report_key, [])
    new_sets = [s for s in sets_for_report if s.get('id') != filter_id]
    user_buckets[report_key] = new_sets
    store[user_id] = user_buckets
    _save_all_filters(store)
    return jsonify({'success': True, 'removed': len(sets_for_report) - len(new_sets)})


# The dataset ID never changes for a given report - no need to re-fetch
# report info on every single call either.
_dataset_id_cache = {}


def _get_dataset_id(access_token):
    config = get_config()
    cache_key = (config['group_id'], config['report_id'])
    if cache_key in _dataset_id_cache:
        return _dataset_id_cache[cache_key]

    url = (f"https://api.powerbi.com/v1.0/myorg/groups/{config['group_id']}"
           f"/reports/{config['report_id']}")
    r = requests.get(url, headers={'Authorization': f'Bearer {access_token}'})
    if r.status_code != 200:
        raise Exception(f"Could not fetch report info: {r.text[:200]}")
    did = r.json().get('datasetId')
    if not did:
        raise Exception("Dataset ID not found in report info")
    _dataset_id_cache[cache_key] = did
    return did


# Skip these common dimension/calendar table names when auto-detecting the
# primary data table from slicer filter targets.
_SKIP_TABLES = {'date', 'calendar', 'dates', 'dim_date', 'time', 'period'}


def _extract_table_name(report_filters, slicer_states):
    """Best-effort: find the dataset table name from filter/slicer targets."""
    candidates = []
    for f in (report_filters or []):
        t = (f.get('target') or {}).get('table', '')
        if t and not t.startswith('$'):
            candidates.append(t)
    for s in (slicer_states or []):
        for sf in ((s.get('state') or {}).get('filters') or []):
            t = (sf.get('target') or {}).get('table', '')
            if t and not t.startswith('$'):
                candidates.append(t)
    # Prefer any table whose name is not a pure dimension/date table
    for t in candidates:
        if t.lower() not in _SKIP_TABLES:
            return t
    return candidates[0] if candidates else None


def _filter_to_dax(f):
    """Convert one Power BI filter object to a DAX boolean expression, or None."""
    target = f.get('target') or {}
    table  = target.get('table', '')
    column = target.get('column', '')
    if not table or not column:
        return None

    col_ref = f"'{table}'[{column}]"

    # Basic filter — values list (In / NotIn)
    if 'values' in f:
        vals = [v for v in (f.get('values') or []) if v is not None]
        if not vals:
            return None
        dax_vals = ', '.join(
            f'"{v}"' if isinstance(v, str) else str(v) for v in vals
        )
        prefix = 'NOT ' if f.get('operator') == 'NotIn' else ''
        return f"{prefix}{col_ref} IN {{{dax_vals}}}"

    # Advanced filter — conditions array
    op_map = {
        'GreaterThan': '>', 'GreaterThanOrEqual': '>=',
        'LessThan': '<',    'LessThanOrEqual': '<=',
        'Is': '=',          'IsNot': '<>',
    }
    parts = []
    for cond in (f.get('conditions') or []):
        op  = op_map.get(cond.get('operator', ''))
        val = cond.get('value')
        if op and val is not None:
            dax_val = f'"{val}"' if isinstance(val, str) else str(val)
            parts.append(f"{col_ref} {op} {dax_val}")
    if parts:
        logic = ' && ' if (f.get('operator') or 'And').lower() == 'and' else ' || '
        return '(' + logic.join(parts) + ')'

    return None


def _build_dax(table, report_filters, slicer_states, row_limit=None):
    row_limit = row_limit if row_limit is not None else EXPORT_ROW_LIMIT
    safe = table.replace("'", "''")
    conditions = []
    for f in (report_filters or []):
        c = _filter_to_dax(f)
        if c:
            conditions.append(c)
    for s in (slicer_states or []):
        for sf in ((s.get('state') or {}).get('filters') or []):
            c = _filter_to_dax(sf)
            if c:
                conditions.append(c)
    if conditions:
        cond_str = ',\n    '.join(conditions)
        return (f"EVALUATE\nCALCULATETABLE(\n    TOPN({row_limit}, '{safe}'),\n"
                f"    {cond_str}\n)")
    return f"EVALUATE TOPN({row_limit}, '{safe}')"


@app.route('/api/export/count', methods=['POST'])
def export_count():
    """Return the number of rows that match the current filters (used to warn before capping at 3,000)."""
    body           = request.get_json(silent=True) or {}
    table          = (body.get('table_override') or '').strip() or None
    report_filters = body.get('report_filters') or []
    slicer_states  = body.get('slicer_states')  or []

    try:
        access_token = get_access_token()
        dataset_id   = _get_dataset_id(access_token)
        headers      = {'Authorization': f'Bearer {access_token}',
                        'Content-Type': 'application/json'}

        if not table:
            table = _extract_table_name(report_filters, slicer_states)
        if not table:
            return jsonify({'count': None}), 200

        safe = table.replace("'", "''")
        conditions = []
        for f in (report_filters or []):
            c = _filter_to_dax(f)
            if c: conditions.append(c)
        for s in (slicer_states or []):
            for sf in ((s.get('state') or {}).get('filters') or []):
                c = _filter_to_dax(sf)
                if c: conditions.append(c)

        if conditions:
            cond_str = ', '.join(conditions)
            dax = f"EVALUATE ROW(\"count\", COUNTROWS(CALCULATETABLE('{safe}', {cond_str})))"
        else:
            dax = f"EVALUATE ROW(\"count\", COUNTROWS('{safe}'))"

        url = f"https://api.powerbi.com/v1.0/myorg/datasets/{dataset_id}/executeQueries"
        qr  = requests.post(url, headers=headers, json={"queries": [{"query": dax}]})
        if qr.status_code != 200:
            return jsonify({'count': None}), 200

        rows = (qr.json().get('results') or [{}])[0].get('tables', [{}])[0].get('rows', [])
        count = list(rows[0].values())[0] if rows else None
        return jsonify({'count': count})
    except Exception:
        return jsonify({'count': None}), 200


@app.route('/api/export/data', methods=['GET', 'POST'])
def export_data():
    """Export a dataset table as CSV or Excel with current Power BI filters applied (max 3,000 rows)."""
    if request.method == 'POST':
        body           = request.get_json(silent=True) or {}
        table          = (body.get('table_override') or '').strip() or None
        report_filters = body.get('report_filters') or []
        slicer_states  = body.get('slicer_states')  or []
        visual_title   = (body.get('visual_title') or '').strip()
        fmt            = (body.get('fmt') or 'csv').strip().lower()
    else:
        table          = (request.args.get('table') or '').strip() or None
        report_filters = []
        slicer_states  = []
        visual_title   = table or 'export'
        fmt            = (request.args.get('fmt') or 'csv').strip().lower()

    if fmt not in ('csv', 'xlsx'):
        fmt = 'csv'

    try:
        access_token = get_access_token()
        dataset_id   = _get_dataset_id(access_token)
        headers      = {'Authorization': f'Bearer {access_token}',
                        'Content-Type': 'application/json'}

        if not table:
            table = _extract_table_name(report_filters, slicer_states)
        if not table:
            return jsonify({
                'success': False,
                'error': ('Could not identify the dataset table. '
                          'Apply at least one slicer then try again, '
                          'or use the manual table-name input.')
            }), 400

        dax = _build_dax(table, report_filters, slicer_states)
        url = f"https://api.powerbi.com/v1.0/myorg/datasets/{dataset_id}/executeQueries"
        qr  = requests.post(url, headers=headers,
                            json={"queries": [{"query": dax}],
                                  "serializerSettings": {"includeNulls": True}})
        if qr.status_code != 200:
            return jsonify({'success': False,
                            'error': f'Query failed ({qr.status_code}): {qr.text[:400]}'}), 500

        rows = (qr.json().get('results') or [{}])[0].get('tables', [{}])[0].get('rows', [])
        if not rows:
            return jsonify({'success': False, 'error': 'No data returned.'}), 404

        def clean_col(name):
            return re.sub(r'^[^[]*\[', '', name).rstrip(']')

        raw_cols   = list(rows[0].keys())
        clean_cols = [clean_col(c) for c in raw_cols]
        base_name  = re.sub(r'[^\w\s-]', '', visual_title or table).strip().replace(' ', '_')

        if fmt == 'xlsx':
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = (visual_title or table)[:31]
            ws.append(clean_cols)
            for cell in ws[1]:
                cell.font = Font(bold=True)
            for row in rows:
                ws.append([row.get(c, '') for c in raw_cols])
            buf = io.BytesIO()
            wb.save(buf)
            buf.seek(0)
            fname = base_name + '.xlsx'
            return Response(
                buf.getvalue(),
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                headers={'Content-Disposition': f'attachment; filename="{fname}"'}
            )
        else:
            buf = io.StringIO()
            w   = csv.writer(buf)
            w.writerow(clean_cols)
            for row in rows:
                w.writerow([row.get(c, '') for c in raw_cols])
            fname = base_name + '.csv'
            return Response(buf.getvalue(), mimetype='text/csv',
                            headers={'Content-Disposition': f'attachment; filename="{fname}"'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/export/convert', methods=['POST'])
def convert_csv_to_xlsx():
    """Accept a CSV string from the browser (visual.exportData result) and return it as Excel."""
    body     = request.get_json(silent=True) or {}
    csv_data = body.get('csv_data', '')
    filename = re.sub(r'[^\w\s-]', '', body.get('filename', 'export')).strip().replace(' ', '_') or 'export'

    if not csv_data:
        return jsonify({'error': 'No CSV data provided.'}), 400

    try:
        reader = csv.reader(io.StringIO(csv_data))
        rows   = list(reader)
        if not rows:
            return jsonify({'error': 'CSV is empty.'}), 400

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = filename[:31]
        for i, row in enumerate(rows):
            ws.append(row)
            if i == 0:
                for cell in ws[1]:
                    cell.font = Font(bold=True)

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return Response(
            buf.getvalue(),
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers={'Content-Disposition': f'attachment; filename="{filename}.xlsx"'}
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ---------------------------------------------------------------------------
# /visualise: chat → Power BI filter
#
# Separate from chatPowerBI (which handles general dashboard Q&A inside the
# embedded report itself, untouched). This box only turns "/visualise ..."
# requests into a real filter, applied via the existing applyFilters() in
# templates/index.html.
#
# The LLM is never trusted to invent column names: it may only choose from
# the field_registry (harvested from the report's own slicers), and its
# output is re-validated against that registry server-side regardless of
# what it returns.
# ---------------------------------------------------------------------------
@app.route('/api/fields', methods=['GET'])
def list_fields():
    """Return the known filterable fields for the current report."""
    registry = _load_field_registry()
    report_key = current_report_key()
    return jsonify({
        'success': True,
        'fields': registry.get(report_key, []),
        'report_bucket': report_key
    })


@app.route('/api/pages/sync', methods=['POST'])
def sync_pages():
    """Record this report's page titles (name + displayName). Called on
    every report load from the browser - costs nothing, needs no slicer,
    and is what lets /visualise guess a page by title match."""
    body = request.get_json(silent=True) or {}
    incoming = body.get('pages')
    if not isinstance(incoming, list):
        return jsonify({'success': False, 'error': 'pages must be a list'}), 400

    report_key = current_report_key()
    registry = _load_page_registry()
    # Preserve hidden_from_nav flags across re-syncs (this runs on every
    # report load) - a plain replace would silently un-hide a page every
    # time the browser reloads.
    prior_hidden = {p['name'] for p in registry.get(report_key, []) if p.get('hidden_from_nav')}

    pages = [
        {
            'name': p.get('name'),
            'displayName': p.get('displayName') or p.get('name'),
            'hidden_from_nav': p.get('name') in prior_hidden
        }
        for p in incoming if isinstance(p, dict) and p.get('name')
    ]
    registry[report_key] = pages
    _save_page_registry(registry)
    return jsonify({'success': True, 'pages': pages})


@app.route('/api/pages/hide', methods=['POST'])
def hide_page():
    """Mark a page as NOT a direct navigation target (e.g. a drill-through
    sub-page like "BS and PL" that's only meant to be reached from within
    another page, never jumped to directly). The model never sees it in its
    page list, so it can't suggest navigating there - but the backend's own
    confirmed-slicer-based resolution (tier 2 of the priority system) still
    uses it correctly if an actual filter field's only known slicer lives
    there, since that logic reads field_registry/page_registry directly and
    never consults the model's page list at all.

    Body: {"page": "<exact page title>", "hidden": true}  (hidden: false to un-hide)
    """
    body = request.get_json(silent=True) or {}
    page_title = (body.get('page') or '').strip()
    hidden = body.get('hidden', True)
    if not page_title:
        return jsonify({'success': False, 'error': '"page" is required'}), 400

    report_key = current_report_key()
    registry = _load_page_registry()
    pages = registry.get(report_key, [])
    resolved = _resolve_page_title(page_title, pages)
    if resolved is None:
        return jsonify({'success': False, 'error': f'"{page_title}" is not a real page in this report'}), 400

    for p in pages:
        if p['name'] == resolved:
            p['hidden_from_nav'] = bool(hidden)
    registry[report_key] = pages
    _save_page_registry(registry)
    return jsonify({'success': True, 'page': resolved, 'hidden_from_nav': bool(hidden)})


@app.route('/api/pages/keywords', methods=['GET'])
def list_page_keywords():
    """Return the taught keyword library for the current report."""
    store = _load_page_keywords()
    return jsonify({'success': True, 'keywords': store.get(current_report_key(), {})})


@app.route('/api/pages/keywords', methods=['POST'])
def add_page_keywords():
    """Teach new keywords for a page, e.g. {"page": "Aircraft Utilisation",
    "keywords": ["hours", "flight cycles"]}. Fixes cases a generic table-
    name/page-title match can't: the data can live in a table whose name
    points at a different page than where it's actually shown."""
    body = request.get_json(silent=True) or {}
    page_title = (body.get('page') or '').strip()
    incoming = body.get('keywords')
    if not page_title or not isinstance(incoming, list):
        return jsonify({'success': False, 'error': '"page" and a "keywords" list are required'}), 400

    report_pages = _load_page_registry().get(current_report_key(), [])
    resolved_page = _resolve_page_title(page_title, report_pages)
    if resolved_page is None:
        return jsonify({'success': False, 'error': f'"{page_title}" is not a real page in this report'}), 400

    new_keywords = {str(k).strip().lower() for k in incoming if str(k).strip()}

    store = _load_page_keywords()
    report_bucket = store.setdefault(current_report_key(), {})
    existing = set(report_bucket.get(resolved_page, []))
    report_bucket[resolved_page] = sorted(existing | new_keywords)
    _save_page_keywords(store)

    return jsonify({'success': True, 'page': resolved_page, 'keywords': report_bucket[resolved_page]})


@app.route('/api/fields/keywords', methods=['POST'])
def add_field_keywords():
    """Teach synonyms for one or more specific slicer fields, e.g.
    {"entries": [{"table": "DIM_COUNTRY", "column": "Country_Name",
    "keywords": ["country", "nation"]}, ...]}. This is the field-level
    counterpart to /api/pages/keywords - it's what lets the model map a
    term straight to the ONE column that actually has a slicer, instead of
    picking a same-meaning column elsewhere that has none."""
    body = request.get_json(silent=True) or {}
    entries = body.get('entries')
    if not isinstance(entries, list):
        return jsonify({'success': False, 'error': 'entries must be a list'}), 400

    registry = _load_field_registry()
    report_key = current_report_key()
    fields = registry.get(report_key, [])
    by_key = {(f['table'], f['column']): f for f in fields}

    updated = []
    skipped = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        table = (e.get('table') or '').strip()
        column = (e.get('column') or '').strip()
        kws = e.get('keywords')
        if not table or not column or not isinstance(kws, list):
            continue
        key = (table, column)
        if key not in by_key:
            skipped.append(f'{table}.{column}')
            continue
        new_kws = {str(k).strip().lower() for k in kws if str(k).strip()}
        existing = set(by_key[key].get('keywords') or [])
        by_key[key]['keywords'] = sorted(existing | new_kws)
        updated.append(f'{table}.{column}')

    registry[report_key] = list(by_key.values())
    _save_field_registry(registry)
    return jsonify({'success': True, 'updated': updated, 'skipped_unknown': skipped})


@app.route('/api/fields/sync', methods=['POST'])
def sync_fields():
    """Merge harvested {table, column, displayName, page, visual} records
    (collected client-side by walking the report's slicer visuals) into the
    registry for the current report."""
    body = request.get_json(silent=True) or {}
    incoming = body.get('fields')
    if not isinstance(incoming, list):
        return jsonify({'success': False, 'error': 'fields must be a list'}), 400

    merged, added = _merge_fields_into_registry(current_report_key(), incoming)
    return jsonify({'success': True, 'fields': merged, 'added': added, 'report_bucket': current_report_key()})


def _merge_fields_into_registry(report_key, incoming):
    """Merge {table, column, displayName, page, visual} records into the
    registry for `report_key`, de-duped by (table, column). Returns
    (merged_list, added_count). Shared by the browser slicer-harvest sync
    and the server-side full-schema sync below."""
    registry = _load_field_registry()
    existing = registry.get(report_key, [])

    by_key = {(f.get('table'), f.get('column')): f
              for f in existing if f.get('table') and f.get('column')}
    added = 0
    for f in incoming:
        table = (f.get('table') or '').strip()
        column = (f.get('column') or '').strip()
        if not table or not column:
            continue
        key = (table, column)
        if key not in by_key:
            added += 1
        # Don't clobber richer metadata (e.g. a slicer-derived displayName,
        # or taught keywords) with a blander re-sync of the same field.
        prior = by_key.get(key) or {}
        by_key[key] = {
            'table': table,
            'column': column,
            'displayName': f.get('displayName') or prior.get('displayName') or '',
            'page': f.get('page') or prior.get('page') or '',
            'visual': f.get('visual') or prior.get('visual') or '',
            'keywords': f.get('keywords') or prior.get('keywords') or []
        }

    merged = list(by_key.values())
    registry[report_key] = merged
    _save_field_registry(registry)
    return merged, added


def _fetch_dataset_schema():
    """Full table/column list for the current report's dataset, straight
    from the model itself via DAX INFO.VIEW functions - no slicer required.
    Hidden tables/columns (date-hierarchy helpers, etc.) are excluded.
    Returns a list of {table, column, displayName} or None on failure."""
    try:
        access_token = get_access_token()
        dataset_id = _get_dataset_id(access_token)
        headers = {'Authorization': f'Bearer {access_token}', 'Content-Type': 'application/json'}
        url = f"https://api.powerbi.com/v1.0/myorg/datasets/{dataset_id}/executeQueries"

        def run_dax(dax):
            r = requests.post(url, headers=headers, json={"queries": [{"query": dax}]}, timeout=30)
            if r.status_code != 200:
                return None
            return (r.json().get('results') or [{}])[0].get('tables', [{}])[0].get('rows', [])

        table_rows = run_dax('EVALUATE INFO.VIEW.TABLES()')
        if table_rows is None:
            return None
        visible_tables = {row.get('[Name]') for row in table_rows
                           if row.get('[Name]') and not row.get('[IsHidden]')}
        if not visible_tables:
            return None

        column_rows = run_dax('EVALUATE INFO.VIEW.COLUMNS()')
        if column_rows is None:
            return None

        fields = []
        for row in column_rows:
            table = row.get('[Table]')
            column = row.get('[Name]')
            if not table or not column or row.get('[IsHidden]'):
                continue
            if table not in visible_tables:
                continue
            fields.append({'table': table, 'column': column, 'displayName': column})
        return fields
    except Exception:
        return None


@app.route('/api/schema/sync', methods=['POST'])
def sync_schema():
    """Pull the full real table/column list straight from the dataset (DAX
    INFO.VIEW functions) and merge it into the field registry. Unlike
    /api/fields/sync this needs no embedded report/browser walk - it talks
    to the Power BI REST API directly - so it can also run automatically
    when the report page first loads (see index())."""
    fields = _fetch_dataset_schema()
    if fields is None:
        return jsonify({'success': False, 'error': 'Could not read the dataset schema.'}), 502
    merged, added = _merge_fields_into_registry(current_report_key(), fields)
    return jsonify({'success': True, 'fields': merged, 'added': added, 'report_bucket': current_report_key()})


# A single /visualise request can call _distinct_values() on the SAME
# column several times over (grounding, candidate checks, sibling search,
# table-consolidation) - cache the result for a few minutes so repeated
# lookups (within one request, or across several in a row) skip the DAX
# round-trip entirely. Distinct values don't change minute-to-minute.
_DISTINCT_VALUES_TTL = 300
_distinct_values_cache = {}


def _distinct_values(table, column, limit=2000):
    """Best-effort: real distinct values for a column, via the existing
    executeQueries pattern. Returns None on any failure (caller falls back
    to using the LLM's values unchanged)."""
    config = get_config()
    cache_key = (config['group_id'], config['report_id'], table, column, limit)
    cached = _distinct_values_cache.get(cache_key)
    if cached and cached[0] > time.time():
        return cached[1]

    try:
        access_token = get_access_token()
        dataset_id = _get_dataset_id(access_token)
        headers = {'Authorization': f'Bearer {access_token}', 'Content-Type': 'application/json'}
        safe_table = table.replace("'", "''")
        dax = f"EVALUATE TOPN({limit}, VALUES('{safe_table}'[{column}]))"
        url = f"https://api.powerbi.com/v1.0/myorg/datasets/{dataset_id}/executeQueries"
        r = requests.post(url, headers=headers, json={"queries": [{"query": dax}]}, timeout=20)
        if r.status_code != 200:
            return None
        rows = (r.json().get('results') or [{}])[0].get('tables', [{}])[0].get('rows', [])
        if not rows:
            result = []
        else:
            key = list(rows[0].keys())[0]
            result = [row.get(key) for row in rows if row.get(key) is not None]
        _distinct_values_cache[cache_key] = (time.time() + _DISTINCT_VALUES_TTL, result)
        return result
    except Exception:
        return None


def _ground_values(table, column, values):
    """Map user/LLM-supplied text values onto the dataset's real distinct
    values (e.g. "UK" -> "United Kingdom"), via exact case-insensitive match
    first, then short-code prefix matching, then fuzzy matching. Leaves a
    value unchanged if nothing matches. A single input value can expand
    into MULTIPLE output values - e.g. "CFM" (an engine family/manufacturer
    prefix, not one specific engine) should select every CFM56-*/LEAP-*
    variant, not get arbitrarily collapsed onto whichever one is textually
    closest and silently drop the rest."""
    distinct = _distinct_values(table, column)
    if not distinct:
        return values
    str_distinct = [str(v) for v in distinct]
    lower_distinct = [d.lower() for d in str_distinct]
    # True only when the column's real values are bare numbers (e.g. a Year
    # dimension of [2025, 2026]) - as opposed to numbers embedded in a label
    # like "FY 2021", where fuzzy/prefix matching a bare "2021" onto the
    # label is still legitimate and must keep working.
    numeric_domain = all(d.isdigit() for d in str_distinct)
    grounded = []
    for v in values:
        sv = str(v)
        sv_lower = sv.lower()

        exact = next((d for d in str_distinct if d.lower() == sv_lower), None)
        if exact is not None:
            grounded.append(exact)
            continue

        # Short alpha(-numeric) codes (ISO country codes like "ALB", or a
        # manufacturer/family prefix like "CFM") checked BEFORE fuzzy
        # matching - a single best-fuzzy-match would otherwise arbitrarily
        # pick one of several equally-valid prefix matches and silently
        # drop the rest, which is wrong when the user means "all of these".
        if 1 < len(sv) <= 6 and sv.replace('-', '').isalnum():
            prefix_hits = [d for d in str_distinct if d.lower().startswith(sv_lower)]
            if prefix_hits:
                grounded.extend(prefix_hits)
                continue

        # A bare numeric input against a purely numeric domain (years, IDs)
        # never goes through text fuzzy matching: difflib scores "2023" vs
        # "2026" at 0.75 just because they share three digits in the same
        # positions, even though they are not remotely the same year. That
        # would silently ground a value with NO real match (e.g. this
        # dataset only has 2025/2026) onto a plausible-looking but wrong
        # number - worse than leaving it unchanged, since the slicer would
        # then confidently show the wrong year's data instead of correctly
        # showing no match for 2023. Doesn't apply when the domain embeds
        # numbers in a label (e.g. "FY 2021") - there, fuzzy-matching a bare
        # "2021" onto its label is still correct and needed.
        if sv.isdigit() and numeric_domain:
            grounded.append(sv)
            continue

        # Case-insensitive fuzzy match (difflib is case-sensitive by
        # default, so "ALB" vs "Albania" scores poorly on case mismatch
        # alone even though it's clearly the right entry).
        close = difflib.get_close_matches(sv_lower, lower_distinct, n=1, cutoff=0.6)
        if close:
            grounded.append(str_distinct[lower_distinct.index(close[0])])
            continue

        grounded.append(sv)
    return grounded


# How many prior turns to keep in a /visualise clarification thread. Kept
# short since it rides in the (client-side, signed) Flask session cookie.
VISUALISE_HISTORY_LIMIT = 8


def _visualise_history_key():
    return current_report_key()


def _get_visualise_history():
    store = session.get('visualise_history') or {}
    return list(store.get(_visualise_history_key(), []))


def _set_visualise_history(history):
    store = session.get('visualise_history') or {}
    store[_visualise_history_key()] = history[-VISUALISE_HISTORY_LIMIT:]
    session['visualise_history'] = store
    session.modified = True


def _build_filter_system_prompt(fields, pages=None, keywords=None):
    """Shared prompt text for whichever LLM provider is active - same
    schema, same rules, so switching providers doesn't change behavior."""
    # Grouped by table (not one line per column) - this is what lets the
    # model reason about *which table* a request belongs to, and stays
    # compact even when the registry holds a whole dataset's real schema.
    by_table = {}
    for f in fields:
        label = f['column']
        if f.get('displayName') and f['displayName'] != f['column']:
            label += f' (shown to users as "{f["displayName"]}")'
        field_kws = f.get('keywords')
        if field_kws:
            # Taught synonyms (e.g. DIM_COUNTRY.Country_Name -> "country",
            # "nation") - this is what lets the model map a term straight to
            # the ONE column that actually has a slicer, first try, instead
            # of relying on the backend's after-the-fact sibling-redirect.
            label += f' (aka: {", ".join(field_kws)})'
        by_table.setdefault(f['table'], []).append(label)
    field_lines = '\n'.join(
        f"- {table}: {', '.join(cols)}" for table, cols in by_table.items()
    )

    # The report's actual page titles double as a keyword library for
    # navigation - "go to deliveries" should work without any filter at all.
    # Taught keywords (e.g. "hours"/"flight cycles" -> "Aircraft Utilisation")
    # fix cases where the data's table name points at the wrong page entirely.
    keywords = keywords or {}
    # Pages marked hidden_from_nav (e.g. a drill-through sub-page like
    # "BS and PL" that's only meant to be reached from within another page,
    # never jumped to directly) are excluded here so the model can't suggest
    # navigating there - its obvious title alone ("BS and PL" = clearly
    # "Balance Sheet and P&L") is enough to get picked even with zero taught
    # keywords, so hiding it from the list is the only reliable fix. This
    # doesn't affect the backend's own confirmed-slicer-based resolution,
    # which reads field_registry/page_registry directly and never consults
    # this list at all.
    visible_pages = [p for p in (pages or []) if not p.get('hidden_from_nav')]
    page_lines_list = []
    for p in sorted(visible_pages, key=lambda p: (p.get('displayName') or p.get('name') or '')):
        title = p.get('displayName') or p.get('name')
        kws = keywords.get(p.get('name'), [])
        if kws:
            page_lines_list.append(f"- {title} (aka: {', '.join(kws)})")
        else:
            page_lines_list.append(f"- {title}")
    page_lines = '\n'.join(page_lines_list) or '(none synced yet)'

    system_prompt = (
        "You convert a user's request into one or more Power BI filter "
        "conditions, or a plain page navigation. Below is the real data "
        "model: table names, each followed by its columns. You may ONLY "
        "filter on a table/column pair that literally appears here - never "
        "invent one, and use this list to work out which table a request "
        "is about:\n\n"
        f"{field_lines}\n\n"
        "This report's actual page titles (for navigation):\n"
        f"{page_lines}\n\n"
        "Respond with ONLY a JSON object (no other text), in one of these shapes:\n\n"
        '{"action": "navigate", "page": "<exact page title from the list above>"}\n'
        '  Use this when the request is PURELY about going somewhere - "go to '
        'deliveries", "show me the jurisdiction risk page", "take me to '
        'orders" - with no filter condition mentioned at all.\n\n'
        'A request can need more than one filter condition at once (e.g. an '
        'entity AND a date range) - put every condition in the "filters" '
        'list, one object per table/column. You may optionally add a '
        'top-level "page" (exact title from the list) if the request is '
        'obviously about one specific page - leave it out if unsure, the '
        'system will guess from the data itself:\n\n'
        '{"action": "filter", "page": "<exact page title, optional>", "filters": [\n'
        '  {"table": "<exact table>", "column": "<exact column>", "operator": "In", "values": ["<value1>"]},\n'
        '  {"table": "<exact table>", "column": "<exact date column>", "operator": "Between", "values": ["<start YYYY-MM-DD>", "<end YYYY-MM-DD>"]},\n'
        '  {"candidates": [{"table": "...", "column": "..."}, {"table": "...", "column": "..."}], "operator": "In", "values": ["<value1>"]}\n'
        ']}\n'
        'or\n'
        '{"action": "clarify", "message": "<short question back to the user>", '
        '"page": "<exact page title, optional>"}\n\n'
        'For "clarify", you may ALSO include "page" if you have any idea at '
        'all which page is relevant even though you can\'t resolve a full '
        'filter - e.g. the request clearly mentions orders-ish things but '
        'nothing concrete enough to filter on. The system double-checks this '
        'the same as any other page reference and only uses it if it\'s a '
        'real page, so a tentative guess is fine - leave it out entirely '
        'only if you truly have no idea.\n\n'
        'Be decisive - only use "clarify" as a last resort:\n'
        '- Table consistency: when a request has multiple conditions (an '
        'entity, a date, etc.), FIRST check whether one single table already '
        'has a column for every condition - prefer putting ALL of them on '
        'that one table rather than spreading them across different tables, '
        'even if another table also happens to have a similarly-named '
        'column. A table whose name matches a word in the request (e.g. '
        '"deliveries" -> a table literally named *DELIVERIES*) is almost '
        'always that one table and should be checked first for ALL of the '
        'request\'s conditions before considering anywhere else.\n'
        '- "navigate" vs "filter": if the request mentions ANY entity, value, '
        'or date to filter on, use "filter" (with an optional "page" hint) - '
        'never "navigate" just because a page name was also mentioned. Use '
        '"navigate" only when there is truly nothing to filter, just a place '
        'to go.\n'
        '- Multiple entries in "filters" are ONLY for genuinely different, '
        'independent conditions in the same request (e.g. an entity name AND a '
        'date range together - resolve both in one response, never ask about one '
        'part while ignoring another).\n'
        '- Do NOT add a separate "filters" entry per table as a way of hedging '
        'when you are unsure which table/column holds the SAME concept (e.g. '
        '"aircraft manufacturer" might be a column in more than one table). That '
        'would wrongly apply the same value as two independent AND\'d conditions '
        'across different tables. Instead collapse it into ONE entry using that '
        'entry\'s "candidates" list - the system checks the real data and picks '
        'the single correct table/column for you.\n'
        '- Column choice: a column tagged "(aka: ...)" has taught synonyms AND '
        'a confirmed on-screen filter control - if the request\'s wording matches '
        'one of those synonyms, use that exact column directly, not a same-named '
        'but untagged column elsewhere. If 2-3 untagged columns could plausibly '
        'hold the same real-world value (e.g. an airline name could sit in a '
        'generic "Customer Name" column or a more specific "Airline_Norm" '
        'column), do NOT ask which one. Use "candidates" as above. Prefer a '
        'column whose name is clearly the canonical/normalized identifier '
        '(contains "Norm", "Code", "ID", or is a dimension table\'s natural key) '
        'as the first candidate, but still list the plausible alternatives.\n'
        '- Date ranges: use operator "Between" with "values": [start, end] in '
        'plain YYYY-MM-DD format (no time component - that\'s added '
        'automatically). NEVER ask the user to confirm a date range - resolve '
        'it yourself, using these rules:\n'
        '  - A month + year ("August 2026") -> the 1st to the last day of that month.\n'
        '  - A bare year ("2025") -> the full calendar year, Jan 1 to Dec 31.\n'
        '  - A quarter ("Q1 2026", "first quarter of 2026") -> that quarter\'s '
        'three months (Q1=Jan-Mar, Q2=Apr-Jun, Q3=Jul-Sep, Q4=Oct-Dec).\n'
        '  - A single named day ("1 March 2026") -> use "In" (not "Between") '
        'with that one exact date, not a range.\n'
        f'  - Relative phrases ("last 6 months", "this year", "year to date", '
        f'"last quarter", "since March") - resolve them against today\'s date, {date.today().isoformat()}.\n'
        '  - Open-ended phrases: "since X"/"after X" -> start=X, end="2100-12-31"; '
        '"before X"/"up to X" -> start="1900-01-01", end=X.\n'
        '- Only use "clarify" when the request genuinely does not map to anything '
        'in the field list above, or is missing information with no sensible '
        'default (e.g. no value given at all). If earlier turns already asked '
        'something and the latest message answers it, use that context to resolve '
        'the filter instead of asking again.\n'
        '- Completeness: before answering, re-read the request and count how '
        'many distinct things it asks to filter on (an entity, a date, a '
        'category, etc.). Your "filters" list MUST have one entry for EVERY '
        'one of them - never silently drop a condition because the schema is '
        'large or another one was harder to resolve. If you genuinely cannot '
        'resolve one of them, use "clarify" and say which part you\'re stuck '
        'on, rather than returning a partial answer that silently omits it.\n'
        '- Typos: the request may contain typos or missing/swapped letters '
        '(e.g. "being" for "Boeing", "custmer" for "customer", "delivries" '
        'for "deliveries"). Silently read past them and resolve what the '
        'user obviously meant - never ask them to fix their spelling, and '
        'never let a typo cause you to drop a condition or pick the wrong '
        'table/column. The actual filter VALUES get matched against real '
        'data separately and don\'t need to be perfectly spelled either.'
    )
    return system_prompt


# Tool schema Claude is forced to call - guarantees a reliably structured
# response every time (forced tool-use is far more reliable than hoping a
# loose text prompt gets followed, which is what was causing Groq's small
# free-tier model to sometimes drop a condition from a multi-part request).
_FILTER_TOOL_SCHEMA = {
    "name": "respond_to_filter_request",
    "description": "Report the resolved filter, navigation, or clarification for the user's request.",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["filter", "navigate", "clarify"]},
            "page": {"type": "string", "description": "Exact page title - required for navigate, optional hint on filter, optional best-guess hint on clarify"},
            "filters": {
                "type": "array",
                "description": "One entry per condition - required for action=filter",
                "items": {
                    "type": "object",
                    "properties": {
                        "table": {"type": "string"},
                        "column": {"type": "string"},
                        "operator": {"type": "string", "enum": ["In", "NotIn", "Between"]},
                        "values": {"type": "array", "items": {"type": "string"}},
                        "candidates": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {"table": {"type": "string"}, "column": {"type": "string"}}
                            }
                        }
                    }
                }
            },
            "message": {"type": "string", "description": "Clarifying question - required for action=clarify"}
        },
        "required": ["action"]
    }
}


def _merge_adjacent_roles(messages):
    """Claude's Messages API expects alternating user/assistant turns.
    Our stored history can have a 'system' self-correction note wedged
    between an assistant reply and the next user turn - fold any
    consecutive same-role entries together rather than sending them as
    separate messages."""
    merged = []
    for m in messages:
        role = 'assistant' if m['role'] == 'assistant' else 'user'
        if merged and merged[-1]['role'] == role:
            merged[-1]['content'] += '\n\n' + m['content']
        else:
            merged.append({'role': role, 'content': m['content']})
    return merged


def _call_claude_for_filter(message, fields, pages=None, keywords=None, history=None):
    """Ask Claude to turn `message` into a filter (or a plain navigation),
    via forced tool-use for a guaranteed structured response. Returns
    (raw_content_str, parsed_dict) - same contract as the Groq caller."""
    if not ANTHROPIC_API_KEY:
        raise RuntimeError('ANTHROPIC_API_KEY is not set')

    system_prompt = _build_filter_system_prompt(fields, pages, keywords)
    messages = _merge_adjacent_roles((history or []) + [{'role': 'user', 'content': message}])

    resp = requests.post(
        'https://api.anthropic.com/v1/messages',
        headers={
            'x-api-key': ANTHROPIC_API_KEY,
            'anthropic-version': '2023-06-01',
            'content-type': 'application/json',
        },
        json={
            'model': CLAUDE_MODEL,
            'max_tokens': 1024,
            'system': system_prompt,
            'messages': messages,
            'tools': [_FILTER_TOOL_SCHEMA],
            'tool_choice': {'type': 'tool', 'name': 'respond_to_filter_request'},
        },
        timeout=20,
    )
    if resp.status_code != 200:
        raise RuntimeError(f'Claude request failed ({resp.status_code}): {resp.text[:300]}')
    body = resp.json()
    tool_use = next((b for b in body.get('content', []) if b.get('type') == 'tool_use'), None)
    if tool_use is None:
        raise RuntimeError('Claude did not return a tool call')
    parsed = tool_use['input']
    return json.dumps(parsed), parsed


def _call_groq_for_filter(message, fields, pages=None, keywords=None, history=None):
    """Ask Groq to turn `message` into a filter (or a plain navigation),
    constrained to `fields` and `pages`, continuing any prior clarification
    turns in `history`. Returns (raw_content_str, parsed_json_dict)."""
    if not GROQ_API_KEY:
        raise RuntimeError('GROQ_API_KEY is not set')

    system_prompt = _build_filter_system_prompt(fields, pages, keywords)
    messages = [{'role': 'system', 'content': system_prompt}]
    messages.extend(history or [])
    messages.append({'role': 'user', 'content': message})

    resp = requests.post(
        'https://api.groq.com/openai/v1/chat/completions',
        headers={'Authorization': f'Bearer {GROQ_API_KEY}', 'Content-Type': 'application/json'},
        json={
            'model': GROQ_MODEL,
            'messages': messages,
            'response_format': {'type': 'json_object'},
            'temperature': 0,
            # gpt-oss is a reasoning model: without an explicit budget it can
            # spend all its tokens on the hidden "reasoning" channel and
            # leave the actual JSON content empty (fails Groq's validator).
            # Low effort + an explicit cap keeps it fast and cheap on the
            # free-tier TPM budget too.
            'max_completion_tokens': 1024,
            'reasoning_effort': 'low',
        },
        timeout=20,
    )
    if resp.status_code != 200:
        raise RuntimeError(f'Groq request failed ({resp.status_code}): {resp.text[:300]}')
    content = resp.json()['choices'][0]['message']['content']
    return content, json.loads(content)


def _call_llm_for_filter(message, fields, pages=None, keywords=None, history=None):
    """Dispatch to whichever provider is configured. Claude is preferred
    when available - forced tool-use makes it far more reliable at not
    silently dropping a condition from a multi-part request, which the
    small free-tier Groq model was prone to."""
    if ANTHROPIC_API_KEY:
        return _call_claude_for_filter(message, fields, pages, keywords, history)
    if GROQ_API_KEY:
        return _call_groq_for_filter(message, fields, pages, keywords, history)
    raise RuntimeError('No LLM provider configured (set ANTHROPIC_API_KEY or GROQ_API_KEY)')


_DATE_ONLY_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def _expand_date_value(v, end_of_day=False):
    """Power BI advanced-filter conditions on Date/DateTime columns need a
    full ISO datetime, not a bare YYYY-MM-DD - a bare date string is often
    silently ignored rather than erroring, which is why date-range filters
    can look "accepted" but have zero effect on the data. Expands it;
    leaves anything else (already-formatted datetimes, numeric ranges) as-is."""
    if isinstance(v, str) and _DATE_ONLY_RE.match(v):
        return f"{v}T23:59:59" if end_of_day else f"{v}T00:00:00"
    return v


def _normalize_label(s):
    """'OD_DELIVERIES' / 'Jurisdiction Risk' -> 'od deliveries' / 'jurisdiction risk' -
    puts table names and page titles in the same comparable form."""
    return re.sub(r'[_\-]+', ' ', s or '').strip().lower()


# Dictionary-based spell-check for the raw /visualise message, run BEFORE
# anything else touches it. The LLM is reasonably typo-tolerant on its own,
# but this app's OWN deterministic matching (_guess_subject_table, page-
# keyword lookups) does plain word comparison and has no such tolerance -
# "delivries" simply isn't "deliveries" to a set-intersection check. A
# character-similarity ratio alone can't safely fix this: "order" vs
# "border" scores HIGHER (0.909) than some genuine typos, so fuzzy-matching
# arbitrary words would create false corrections. A real dictionary doesn't
# have that problem - it already knows "border" is a correctly-spelled word
# and leaves it alone, while "delivries" isn't a word at all and gets fixed.
_spell = SpellChecker()


def _correct_typos(message):
    """Conservatively fix likely misspellings of common English words.
    Skips: proper nouns (capitalized - airline/company/person names),
    short words (<=4 chars - protects acronyms/codes like "CFM", "MLA"
    from being "corrected" into an unrelated real word), non-alphabetic
    tokens, and anything already a recognized dictionary word."""
    words = message.split()
    out = []
    for w in words:
        core = w.rstrip('.,!?;:')  # trailing punctuation only; leading is rare enough to skip
        suffix = w[len(core):]
        if len(core) <= 4 or not core.isalpha() or core[0].isupper() or core.lower() in _spell:
            out.append(w)
            continue
        fix = _spell.correction(core.lower())
        out.append((fix + suffix) if fix else w)
    return ' '.join(out)


def _guess_page_for_table(table, pages):
    """Best-effort: match a table name against this report's actual page
    titles (e.g. table 'OD_DELIVERIES' <-> a page titled 'Deliveries'), no
    slicer required. Returns the page's `name` (for activatePageByName) if
    there's a reasonably confident match, else None."""
    if not pages:
        return None
    target = _normalize_label(table)
    target_words = set(target.split())
    best_name, best_score = None, 0.0
    for p in pages:
        label = _normalize_label(p.get('displayName') or p.get('name') or '')
        if not label:
            continue
        ratio = difflib.SequenceMatcher(None, target, label).ratio()
        label_words = set(label.split())
        overlap = len(target_words & label_words) / max(1, len(target_words | label_words))
        score = max(ratio, overlap)
        if score > best_score:
            best_score, best_name = score, p.get('name')
    return best_name if best_score >= 0.45 else None


def _confirmed_page_for_table(table, fields):
    """If ANY field on this table has a confirmed slicer binding (ground
    truth from the slicer sweep), return its page - even if the CURRENT
    condition's own column has no slicer of its own. Without this, a
    condition on a slicer-less column (e.g. TIRALIS_AIRLINE_FINANCIAL.METRIC)
    falls through to _guess_page_for_table's pure title-fuzzy-match, which
    can land on a different, wrong page than where this table's OTHER,
    confirmed fields actually live (e.g. "Airline Financials" by title
    similarity, when the table's real confirmed page is "BS and PL") -
    splitting one table's conditions across two pages in the same request."""
    for f in fields:
        if f.get('table') == table and f.get('page') and f.get('visual'):
            return f['page']
    return None


def _resolve_unknown_field(table, column, fields):
    """Fuzzy-correct a hallucinated/misnamed (table, column) to the closest
    real field instead of giving up immediately - e.g. the model saying
    "AIRLINE" on "OD_ORDERS" should land on the real "Airline_Norm" column
    there. Tries the same table first (a typo'd/abbreviated column name is
    far more likely than a wrong table too), then falls back to any table.
    Returns (table, column) if there's a confident match, else None."""
    if not column:
        return None
    target = _normalize_label(column)

    def best_match(candidates):
        best, best_score = None, 0.0
        for f in candidates:
            label = _normalize_label(f['column'])
            if not label:
                continue
            ratio = difflib.SequenceMatcher(None, target, label).ratio()
            if target in label or label in target:
                ratio = max(ratio, 0.8)
            if ratio > best_score:
                best_score, best = ratio, f
        return best, best_score

    same_table = [f for f in fields if f['table'] == table]
    best, score = best_match(same_table)
    if best and score >= 0.5:
        return best['table'], best['column']

    best, score = best_match(fields)
    if best and score >= 0.6:
        return best['table'], best['column']

    return None


def _match_level(term, distinct_lower):
    """Match quality of `term` against a column's real (lowercased) distinct
    values: 3 = exact, 2 = close fuzzy match, 1 = substring either way
    (weakest - a lessor-prefixed entry like "calc (air india)" merely
    containing "air india" shouldn't outrank a column with an exact
    "air india" entry), 0 = no match."""
    if term in distinct_lower:
        return 3
    if difflib.get_close_matches(term, distinct_lower, n=1, cutoff=0.75):
        return 2
    if any(term in d or d in term for d in distinct_lower):
        return 1
    return 0


def _resolve_candidate_column(candidates, known, values, fields):
    """When the model listed several plausible {table, column} pairs for
    the same value instead of asking which one, probe each via its real
    distinct values (DAX) and auto-pick whichever one matches best.
    Returns (resolved_table_column_or_None, suggestions). `suggestions` is
    {(table, column): [close real values]} for any candidate that had no
    match - lets the caller's clarify message show what IS actually there,
    instead of just naming the columns."""
    text_values = [str(v).lower() for v in values]
    scored = []
    suggestions = {}
    for c in candidates:
        if not isinstance(c, dict):
            continue
        table = (c.get('table') or '').strip()
        column = (c.get('column') or '').strip()
        if not table or not column:
            continue
        if (table, column) not in known:
            corrected = _resolve_unknown_field(table, column, fields)
            if corrected is None:
                continue
            table, column = corrected
        distinct = _distinct_values(table, column)
        if not distinct:
            continue
        str_distinct = [str(v).lower() for v in distinct]
        level = max((_match_level(tv, str_distinct) for tv in text_values), default=0)
        if level > 0:
            scored.append((level, table, column))
        else:
            close = []
            for tv in text_values:
                close.extend(difflib.get_close_matches(tv, str_distinct, n=3, cutoff=0.4))
            if close:
                suggestions[(table, column)] = sorted(set(close))[:5]
    if not scored:
        return None, suggestions
    # On a tie, prefer whichever candidate the model listed first - it was
    # told to put the canonical/normalized column first, so a real tie in
    # the data (e.g. "AIR INDIA" appearing verbatim in two columns) still
    # resolves to its stated preference instead of asking the user.
    best_level = max(s[0] for s in scored)
    return next((s[1], s[2]) for s in scored if s[0] == best_level), {}


def _validate_one_filter_spec(spec, known, fields, subject_table=None):
    """Validate+build a single filter condition. Returns
    (filter_obj, None, table, column) on success, or
    (None, clarify_message, None, None) on failure. Never trusts the
    model: table/column (direct, fuzzy-corrected, or resolved via
    candidates) is re-checked against the field registry regardless of
    what it claims."""
    if not isinstance(spec, dict):
        return None, "I couldn't understand part of that request.", None, None

    table = spec.get('table')
    column = spec.get('column')
    operator = spec.get('operator') or 'In'
    values = spec.get('values')

    candidates = spec.get('candidates')
    if isinstance(candidates, list) and len(candidates) >= 2:
        if not isinstance(values, list) or not values:
            return None, 'What value should I filter to?', None, None
        resolved, suggestions = _resolve_candidate_column(candidates, known, values, fields)
        if resolved is None:
            names = ', '.join(
                f"{c.get('table')}.{c.get('column')}" for c in candidates
                if isinstance(c, dict) and c.get('table') and c.get('column')
            )
            vals = ', '.join(str(v) for v in values)
            extra = ''
            if suggestions:
                parts = [f'{t}.{c} has: {", ".join(opts)}' for (t, c), opts in suggestions.items()]
                extra = ' Closest real values I found - ' + '; '.join(parts) + '.'
            return None, (
                f'I checked {names} but couldn\'t find "{vals}" in exactly one of '
                f'them - which field did you mean?{extra}'
            ), None, None
        table, column = resolved

    if (table, column) not in known:
        # The model may have named a close-but-wrong column (e.g. "AIRLINE"
        # instead of the real "Airline_Norm") - try to self-correct before
        # giving up and asking the user to rephrase.
        corrected = _resolve_unknown_field(table, column, fields)
        if corrected is not None:
            table, column = corrected

    if (table, column) not in known:
        return None, (
            f'I don\'t recognize "{column}" on "{table}" in this report. '
            'Try rephrasing using one of the fields this report actually filters on.'
        ), None, None

    # The model picked a real, valid column that just has no slicer on
    # screen (e.g. "JURISDICTION_RISK_ANALYSIS.Country" is valid, but the
    # visible Country dropdown is actually bound to "DIM_COUNTRY.Country_Name").
    # Redirect the WHOLE filter to that slicer-bound sibling rather than
    # adding a second filter alongside the original - setting both
    # independently risks over-constraining the model to zero rows if the
    # two tables aren't related the way that'd require.
    if operator != 'Between' and isinstance(values, list) and values:
        has_own_slicer = any(
            f['table'] == table and f['column'] == column and f.get('visual') for f in fields
        )
        if not has_own_slicer:
            sibling = _find_slicer_sibling(table, column, values, fields)
            if sibling:
                table, column = sibling['table'], sibling['column']

    # Table consistency wins over the slicer-visual preference above: if
    # the whole request is clearly about one subject table (e.g.
    # "deliveries" -> OD_DELIVERIES, from the user's own wording) but this
    # condition resolved to a DIFFERENT table, prefer an equivalent column
    # within that subject table instead - even if that costs the slicer
    # redirect just applied. A correct, self-consistent answer (all
    # conditions on the same table) matters more than one control visually
    # moving on an unrelated table.
    #
    # Exception: don't redirect AWAY from a shared dimension table
    # (DIM_COUNTRY, DIM_DATE, DIM_AIRLINE, ...) just because of a subject-
    # table guess. In a star schema, dimension tables are DESIGNED to be
    # reused across many subject areas via relationships - e.g. "jurisdiction
    # risk of albania" resolving to DIM_COUNTRY (not JURISDICTION_RISK_
    # ANALYSIS's own denormalized country column) is a perfectly correct,
    # often-better choice, not a mistake to correct. The redirect exists to
    # catch the model wandering into a genuinely unrelated FACT/analysis
    # table (e.g. a "deliveries" question landing on an airline-financials
    # table purely because it also has a column literally named "AIRLINE"),
    # not to force every query onto one table regardless of schema design.
    if (subject_table and table != subject_table and not _is_dimension_table(table)
            and isinstance(values, list) and values):
        equiv = _find_equivalent_column(
            table, column, values, fields, restrict_to_table=subject_table, require_visual=False
        )
        if equiv:
            table, column = equiv['table'], equiv['column']

    if operator == 'Between':
        if not isinstance(values, list) or len(values) != 2:
            return None, 'What date (or number) range should I filter to?', None, None
        return {
            '$schema': 'http://powerbi.com/product/schema#advanced',
            'target': {'table': table, 'column': column},
            'filterType': 0,
            'logicalOperator': 'And',
            'conditions': [
                {'operator': 'GreaterThanOrEqual', 'value': _expand_date_value(values[0])},
                {'operator': 'LessThanOrEqual', 'value': _expand_date_value(values[1], end_of_day=True)},
            ]
        }, None, table, column

    if not isinstance(values, list) or not values:
        return None, 'What value(s) should I filter to?', None, None
    if operator not in ('In', 'NotIn'):
        operator = 'In'

    str_values = [str(v) for v in values]
    if all(_DATE_ONLY_RE.match(v) for v in str_values):
        # Exact date(s): use verbatim (just expanded to a full datetime,
        # same as the Between path) - never fuzzy-match these against real
        # data. Text-similarity doesn't understand dates: "2026-08-01" and
        # "2025-08-01" differ by one character, so a wrong-year date can
        # textually outscore the correct date that's merely a few weeks away.
        final_values = [_expand_date_value(v) for v in str_values]
    else:
        final_values = _ground_values(table, column, str_values)

    return {
        '$schema': 'http://powerbi.com/product/schema#basic',
        'target': {'table': table, 'column': column},
        'filterType': 1,
        'operator': operator,
        'values': final_values
    }, None, table, column


def _find_equivalent_column(table, column, values, fields, restrict_to_table=None, require_visual=True):
    """Find a column elsewhere that represents the SAME real-world concept
    as (table, column) - related either by column-name similarity OR by
    sharing a taught keyword (catches cases like "Register_Alpha3" vs
    "Country_Name": same concept, completely different naming convention,
    so name-similarity alone would never connect them) - then probes its
    real data to confirm it's a true match before using it. Pass
    restrict_to_table to only look within one specific table (table-
    consolidation use case); require_visual=False to also consider columns
    with no slicer of their own. Returns the matching field dict or None."""
    source = next((f for f in fields if f['table'] == table and f['column'] == column), None)
    source_keywords = set(source.get('keywords') or []) if source else set()

    target = _normalize_label(column)
    text_values = [str(v).lower() for v in values]
    scored = []
    for f in fields:
        if (f['table'], f['column']) == (table, column):
            continue
        if require_visual and not f.get('visual'):
            continue
        if restrict_to_table and f['table'] != restrict_to_table:
            continue
        label = _normalize_label(f['column'])
        ratio = difflib.SequenceMatcher(None, target, label).ratio()
        if target in label or label in target:
            ratio = max(ratio, 0.8)
        shares_keyword = bool(source_keywords & set(f.get('keywords') or []))
        if ratio < 0.6 and not shares_keyword:
            continue
        distinct = _distinct_values(f['table'], f['column'])
        if not distinct:
            continue
        str_distinct = [str(v).lower() for v in distinct]
        level = max((_match_level(tv, str_distinct) for tv in text_values), default=0)
        if level > 0:
            scored.append((level, f))
    if not scored:
        return None
    best_level = max(lvl for lvl, _ in scored)
    return next(f for lvl, f in scored if lvl == best_level)


def _find_slicer_sibling(table, column, values, fields):
    """A visible slicer bound to the SAME field as a resolved filter lets
    Power BI's own control reflect the selection. See
    _find_equivalent_column for the matching principle."""
    return _find_equivalent_column(table, column, values, fields, require_visual=True)


def _is_dimension_table(table):
    """Recognize this schema's shared-dimension naming convention
    (DIM_COUNTRY, DIM_DATE, DIM_AIRLINE, ...). Dimension tables are meant to
    be reused across many subject areas via relationships in a star schema -
    table-consolidation should never force a redirect away from one just
    because a subject-table guess points elsewhere."""
    return (table or '').strip().upper().startswith('DIM')


def _guess_subject_table(message, fields):
    """Which real table does the user's own wording most directly name?
    (e.g. "deliveries" in the message -> OD_DELIVERIES). This is a much
    stronger signal than trusting the model's own table choice for every
    condition in a multi-condition request - it's grounded in the user's
    literal words, not reasoning that can drift across unrelated tables.

    Scored by count of DISTINCTIVE matching words, not a dilutable ratio -
    a 5-word table name like AIRCRAFT_REGISTRY_FLEET_DATA_FINAL would never
    cross a ratio threshold from one matched word alone. Words that appear
    in many table names (generic suffixes, or an overloaded term like
    "aircraft" shared by several tables) are excluded - they're not
    distinctive enough to confidently pick ONE table."""
    msg_words = set(_normalize_label(message).split())
    if not msg_words:
        return None
    tables = sorted({f['table'] for f in fields})
    table_words = {t: set(_normalize_label(t).split()) for t in tables}

    word_table_count = {}
    for words in table_words.values():
        for w in words:
            word_table_count[w] = word_table_count.get(w, 0) + 1

    best, best_score = None, 0
    for t, label_words in table_words.items():
        if not label_words:
            continue
        distinctive_hits = [
            w for w in (msg_words & label_words)
            if len(w) > 2 and word_table_count[w] <= 2
        ]
        score = len(distinctive_hits)
        if score > best_score:
            best_score, best = score, t
    return best if best_score >= 1 else None


def _resolve_page_title(title, report_pages):
    """Exact (case-insensitive) match of a model-stated page title against
    this report's real page list. Returns the internal page `name` (for
    activatePageByName) or None - never trust the title blindly."""
    if not title:
        return None
    t = title.strip().lower()
    for p in report_pages:
        label = (p.get('displayName') or p.get('name') or '').strip().lower()
        if label == t:
            return p.get('name')
    return None


def _guess_best_page_for_message(message, fields, page_hint=None):
    """Best-effort page suggestion for an ambiguous/unresolved request -
    "I couldn't understand that, but it looks like you might be looking for
    something on the 'Orders' page." Tries the model's own page hint first
    (if it supplied one even while clarifying, validated the same as any
    other page reference - never trusted blindly), then falls back to the
    same distinctive-word subject-table guess used for table-consolidation,
    resolved to its real page via the normal confirmed-slicer-first chain.
    Returns (internal_page_name, display_name) or (None, None)."""
    report_pages = _load_page_registry().get(current_report_key(), [])

    if page_hint:
        resolved = _resolve_page_title(page_hint, report_pages)
        if resolved:
            display = next((p['displayName'] for p in report_pages if p['name'] == resolved), resolved)
            return resolved, display

    subject_table = _guess_subject_table(message, fields)
    if not subject_table:
        return None, None
    confirmed_page = next(
        (f.get('page') for f in fields if f['table'] == subject_table and f.get('page')), None
    )
    page = confirmed_page or _guess_page_for_table(subject_table, report_pages)
    if not page:
        return None, None
    display = next((p['displayName'] for p in report_pages if p['name'] == page), page)
    return page, display


def _enrich_clarify(msg, message, fields, page_hint=None):
    """Wrap a bare clarify string with a best-effort page suggestion, if
    one can be found with any confidence. The single place every clarify
    return in _validate_and_build_filter funnels through, so the
    enrichment logic lives in exactly one spot."""
    page, display = _guess_best_page_for_message(message, fields, page_hint)
    result = {'clarify': msg}
    if page and display:
        result['suggested_page'] = page
        result['suggested_page_name'] = display
    return result


def _validate_and_build_filter(parsed, fields, message=''):
    """Top-level response handler. A "filter" action carries one or more
    conditions in "filters" (e.g. an entity AND a date range at once);
    each is validated independently via _validate_one_filter_spec, which
    also consolidates each condition onto a shared "subject table" guessed
    from `message` (e.g. "deliveries" -> OD_DELIVERIES) when the model
    scattered them across unrelated tables. A "navigate" action is a pure
    page-jump with no filter at all. Returns {'filters': [...], 'slicers':
    [...], 'pages': [...]} (ready for applyFilters()) or {'clarify': '...'}."""
    if not isinstance(parsed, dict):
        return _enrich_clarify("I couldn't understand that. Could you rephrase it?", message, fields)

    action = parsed.get('action')
    if action == 'clarify':
        return _enrich_clarify(
            parsed.get('message') or 'Could you rephrase that?', message, fields, parsed.get('page')
        )

    report_pages = _load_page_registry().get(current_report_key(), [])

    if action == 'navigate':
        resolved_page = _resolve_page_title(parsed.get('page'), report_pages)
        if resolved_page is None:
            return _enrich_clarify((
                f'I don\'t recognize "{parsed.get("page")}" as a page in this report. '
                'Try naming one of this report\'s actual page titles.'
            ), message, fields)
        return {'filters': [], 'slicers': [], 'pages': [resolved_page], 'navigated_only': True}

    if action != 'filter':
        return _enrich_clarify("I couldn't turn that into a filter. Could you rephrase it?", message, fields)

    known = {(f['table'], f['column']) for f in fields}
    visual_by_field = {(f['table'], f['column']): (f.get('page'), f.get('visual')) for f in fields}
    hinted_page = _resolve_page_title(parsed.get('page'), report_pages)

    specs = parsed.get('filters')
    if not isinstance(specs, list) or not specs:
        # Backward-compat: tolerate a flat single-condition shape at the
        # top level instead of a "filters" list.
        specs = [{
            'table': parsed.get('table'),
            'column': parsed.get('column'),
            'operator': parsed.get('operator'),
            'values': parsed.get('values'),
            'candidates': parsed.get('candidates'),
        }]

    # Runs for single-condition requests too - "deliveries to Air India" is
    # one filter condition, but still needs correcting if the model picked
    # a table that has nothing to do with deliveries. The dimension-table
    # exception above (not this count) is what protects legitimate single-
    # condition dimension lookups like "jurisdiction risk of albania".
    subject_table = _guess_subject_table(message, fields)

    filter_objs = []
    slicer_objs = []
    # Pages get filled in per-condition below, from confirmed slicer
    # bindings or table-name guesses - NOT pre-seeded from the model's own
    # page hint. A hint like "Aircraft Registry" can look very plausible
    # (the table is literally named AIRCRAFT_REGISTRY_FLEET_DATA_FINAL) while
    # being flat wrong about where the resolved fields' slicers actually
    # live. The hint is only appended as a fallback, after the loop, if the
    # resolved conditions didn't yield any page themselves.
    pages = []
    for spec in specs:
        obj, clarify, table, column = _validate_one_filter_spec(spec, known, fields, subject_table)
        if clarify:
            return _enrich_clarify(clarify, message, fields, parsed.get('page'))

        # If a visible slicer is bound to this exact field, drive ONLY the
        # slicer, not a report-level filter on top of it. Running both
        # simultaneously on the same field is redundant at best and
        # confusing at worst: the report-level filter still cross-filters
        # the page (visibly shrinking the slicer's own dropdown to the
        # matching value), while the slicer's OWN "checked" state is a
        # separate thing that call doesn't set - so the box looks empty
        # even though the data is correctly filtered. Selecting a value in
        # a slicer already filters the whole page by itself; nothing extra
        # is needed. (table, column) here may already have been redirected
        # to a slicer-bound sibling inside _validate_one_filter_spec.
        page, visual = visual_by_field.get((table, column), (None, None))
        if page and visual:
            slicer_objs.append({
                'page': page,
                'pageDisplayName': '',
                'visual': visual,
                'visualTitle': '',
                'state': {'filters': [obj], 'targets': [{'table': table, 'column': column}]}
            })
        else:
            filter_objs.append(obj)

        # Prefer a slicer-confirmed page (exact, either this field's own or
        # a sibling field's on the same table); fall back to a title guess
        # (e.g. "OD_DELIVERIES" <-> "Deliveries") only if nothing on this
        # table has a confirmed binding at all.
        page = page or _confirmed_page_for_table(table, fields) or _guess_page_for_table(table, report_pages)
        if page and page not in pages:
            pages.append(page)

    if hinted_page and hinted_page not in pages:
        pages.append(hinted_page)

    return {'filters': filter_objs, 'slicers': slicer_objs, 'pages': pages}


@app.route('/api/visualise', methods=['POST'])
def visualise():
    body = request.get_json(silent=True) or {}
    message = (body.get('message') or '').strip()
    if not message:
        return jsonify({'clarify': 'Type what you want to filter the report to.'})
    # Fix typos BEFORE anything else sees the message - both the LLM and
    # this app's own deterministic matching (_guess_subject_table, page-
    # keyword lookups) benefit, and the latter has zero typo-tolerance
    # otherwise (plain word comparison, not fuzzy).
    message = _correct_typos(message)

    registry = _load_field_registry()
    fields = registry.get(current_report_key(), [])
    if not fields:
        return jsonify({'clarify': 'No report fields are synced yet — reload the report page and try again.'})

    report_pages = _load_page_registry().get(current_report_key(), [])
    report_keywords = _load_page_keywords().get(current_report_key(), {})
    history = _get_visualise_history()

    try:
        raw_content, parsed = _call_llm_for_filter(message, fields, report_pages, report_keywords, history)
    except Exception as e:
        return jsonify({'clarify': f'Could not reach the filter assistant: {e}'}), 502

    result = _validate_and_build_filter(parsed, fields, message)

    if 'filters' in result:
        # Resolved - next message starts a fresh thread.
        _set_visualise_history([])
    else:
        new_history = history + [
            {'role': 'user', 'content': message},
            {'role': 'assistant', 'content': raw_content},
        ]
        # If the model claimed "filter" but our own validation rejected it
        # (bad table/column, missing values), tell it why so the next turn
        # can self-correct instead of repeating the same mistake.
        if parsed.get('action') == 'filter':
            new_history.append({'role': 'system', 'content': result['clarify']})
        _set_visualise_history(new_history)

    return jsonify(result)


@app.route('/api/visualise/reset', methods=['POST'])
def visualise_reset():
    """Abandon the current clarification thread and start fresh."""
    _set_visualise_history([])
    return jsonify({'success': True})


if __name__ == '__main__':
    print("=" * 50)
    print("Power BI Embedded Demo Starting...")
    print("=" * 50)
    print(f"Main App: http://localhost:8080/")
    print(f"Test API: http://localhost:8080/test")
    print(f"Health:   http://localhost:8080/health")
    print("=" * 50)
    print("Default Config:")
    print(f"   Tenant:  {DEFAULT_CONFIG['tenant_id']}")
    print(f"   Group:   {DEFAULT_CONFIG['group_id']}")
    print(f"   Report:  {DEFAULT_CONFIG['report_id']}")
    print("=" * 50)

    app.run(host='0.0.0.0', port=8080, debug=True)

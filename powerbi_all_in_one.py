from flask import Flask, render_template_string, jsonify
import requests
import json
from datetime import datetime

app = Flask(__name__)

# Your Power BI Configuration
CONFIG = {
    'tenant_id': '65693725-b6ed-4482-838d-454f6f79eafb',
    'client_id': '8264d2da-a712-455b-bb14-6e10d4db8676',
    'client_secret': 'D128Q~XhARPUB_ddynizWtw-Mouah6GTOCkPKb-C',
    'group_id': '77200bef-3c01-4ece-b20a-10de91382e51',
    'report_id': 'b411babc-f8e8-4a01-b51c-39c1f9560087'
}

# Complete HTML Template with Power BI Embedded
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Eby's Secure Power BI Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/powerbi-client@2.22.0/dist/powerbi.min.js"></script>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        
        .header {
            background: rgba(255, 255, 255, 0.95);
            backdrop-filter: blur(10px);
            padding: 25px;
            border-radius: 15px;
            margin-bottom: 20px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.1);
            text-align: center;
        }
        
        .header h1 {
            color: #333;
            font-size: 2.2em;
            margin-bottom: 10px;
            background: linear-gradient(135deg, #667eea, #764ba2);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        
        .status {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 10px 20px;
            border-radius: 25px;
            font-weight: 500;
            transition: all 0.3s ease;
        }
        
        .status.loading {
            background: #fff3cd;
            color: #856404;
            border: 1px solid #ffeaa7;
        }
        
        .status.success {
            background: #d4edda;
            color: #155724;
            border: 1px solid #c3e6cb;
        }
        
        .status.error {
            background: #f8d7da;
            color: #721c24;
            border: 1px solid #f5c6cb;
        }
        
        .report-container {
            background: white;
            border-radius: 15px;
            overflow: hidden;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.1);
            height: 700px;
            position: relative;
        }
        
        #reportContainer {
            width: 100%;
            height: 100%;
            border: none;
        }
        
        .loading-screen {
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            height: 100%;
            background: #f8f9fa;
        }
        
        .spinner {
            width: 50px;
            height: 50px;
            border: 4px solid #f3f3f3;
            border-top: 4px solid #667eea;
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin-bottom: 20px;
        }
        
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        
        .error-screen {
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            height: 100%;
            padding: 40px;
            text-align: center;
            color: #721c24;
            background: #f8d7da;
        }
        
        .debug-panel {
            position: fixed;
            bottom: 20px;
            right: 20px;
            background: rgba(255, 255, 255, 0.9);
            padding: 15px;
            border-radius: 10px;
            box-shadow: 0 4px 16px rgba(0, 0, 0, 0.1);
            max-width: 300px;
            font-size: 12px;
        }
        
        .debug-panel h4 {
            margin-bottom: 10px;
            color: #333;
        }
        
        .debug-item {
            margin-bottom: 5px;
            display: flex;
            justify-content: space-between;
        }
        
        .refresh-btn {
            margin-top: 20px;
            padding: 12px 24px;
            background: linear-gradient(135deg, #667eea, #764ba2);
            color: white;
            border: none;
            border-radius: 25px;
            cursor: pointer;
            font-weight: 500;
            transition: transform 0.2s ease;
        }
        
        .refresh-btn:hover {
            transform: translateY(-2px);
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🔐 Secure Power BI Dashboard</h1>
            <div id="status" class="status loading">🔄 Initializing...</div>
        </div>
        
        <div class="report-container">
            <div id="reportContainer">
                <div class="loading-screen">
                    <div class="spinner"></div>
                    <h3>Loading Power BI Report...</h3>
                    <p>Please wait while we authenticate and load your dashboard</p>
                </div>
            </div>
        </div>
        
        <div class="debug-panel" id="debugPanel">
            <h4>📊 Debug Info</h4>
            <div class="debug-item">
                <span>Status:</span>
                <span id="debugStatus">Starting...</span>
            </div>
            <div class="debug-item">
                <span>Token:</span>
                <span id="debugToken">Pending...</span>
            </div>
            <div class="debug-item">
                <span>Report:</span>
                <span id="debugReport">Loading...</span>
            </div>
        </div>
    </div>

    <script>
        let report = null;
        let debugInfo = {
            status: 'Starting...',
            token: 'Pending...',
            report: 'Loading...'
        };

        function updateStatus(message, type = 'loading') {
            const statusElement = document.getElementById('status');
            statusElement.textContent = message;
            statusElement.className = `status ${type}`;
            
            debugInfo.status = message;
            updateDebugPanel();
        }

        function updateDebugPanel() {
            document.getElementById('debugStatus').textContent = debugInfo.status;
            document.getElementById('debugToken').textContent = debugInfo.token;
            document.getElementById('debugReport').textContent = debugInfo.report;
        }

        function showError(message) {
            const container = document.getElementById('reportContainer');
            container.innerHTML = `
                <div class="error-screen">
                    <h2>❌ Error Loading Report</h2>
                    <p>${message}</p>
                    <button class="refresh-btn" onclick="location.reload()">🔄 Retry</button>
                </div>
            `;
            updateStatus('❌ Error occurred', 'error');
            debugInfo.report = 'Error: ' + message;
            updateDebugPanel();
        }

        async function loadPowerBIReport() {
            try {
                updateStatus('🔑 Authenticating with Azure AD...');
                
                // Get embed token from our Flask API
                const response = await fetch('/api/embed-token');
                const data = await response.json();
                
                if (!response.ok || !data.success) {
                    throw new Error(data.error || `HTTP ${response.status}: ${response.statusText}`);
                }
                
                debugInfo.token = data.token ? 'Obtained ✅' : 'Missing ❌';
                updateStatus('🔄 Loading Power BI report...', 'loading');
                
                // Configure Power BI embed
                const models = window['powerbi-client'].models;
                const config = {
                    type: 'report',
                    id: data.reportId,
                    embedUrl: data.embedUrl,
                    accessToken: data.token,
                    tokenType: models.TokenType.Embed,
                    settings: {
                        filterPaneEnabled: true,
                        navContentPaneEnabled: true,
                        panes: {
                            filters: {
                                expanded: false,
                                visible: true
                            }
                        },
                        background: models.BackgroundType.Transparent
                    }
                };
                
                // Embed the report
                const reportContainer = document.getElementById('reportContainer');
                report = powerbi.embed(reportContainer, config);
                
                // Event handlers
                report.on('loaded', function() {
                    updateStatus('✅ Report loaded successfully!', 'success');
                    debugInfo.report = 'Loaded ✅';
                    updateDebugPanel();
                    console.log('Power BI report loaded successfully');
                });
                
                report.on('rendered', function() {
                    updateStatus('🎨 Report rendered and ready!', 'success');
                    console.log('Power BI report rendered');
                });
                
                report.on('error', function(event) {
                    const error = event.detail;
                    console.error('Power BI report error:', error);
                    showError(`Report Error: ${error.message || 'Unknown error'}`);
                });
                
            } catch (error) {
                console.error('Failed to load Power BI report:', error);
                showError(error.message);
            }
        }

        // Initialize when page loads
        document.addEventListener('DOMContentLoaded', function() {
            console.log('🚀 Starting Power BI embed process...');
            setTimeout(loadPowerBIReport, 500); // Small delay for better UX
        });

        // Refresh functionality
        window.refreshReport = function() {
            if (report) {
                report.refresh();
                updateStatus('🔄 Refreshing data...', 'loading');
            }
        };
    </script>
</body>
</html>
'''

def get_access_token():
    """Get Azure AD access token for Power BI API"""
    try:
        token_url = f"https://login.microsoftonline.com/{CONFIG['tenant_id']}/oauth2/v2.0/token"
        token_data = {
            'grant_type': 'client_credentials',
            'client_id': CONFIG['client_id'],
            'client_secret': CONFIG['client_secret'],
            'scope': 'https://analysis.windows.net/powerbi/api/.default'
        }
        
        print(f"🔑 Requesting access token from: {token_url}")
        response = requests.post(token_url, data=token_data, timeout=30)
        
        if response.status_code != 200:
            error_detail = response.json() if response.headers.get('content-type') == 'application/json' else response.text
            raise Exception(f"Token request failed (Status {response.status_code}): {error_detail}")
            
        token_response = response.json()
        access_token = token_response['access_token']
        print("✅ Access token obtained successfully")
        return access_token
        
    except requests.exceptions.Timeout:
        raise Exception("Token request timed out")
    except requests.exceptions.RequestException as e:
        raise Exception(f"Token request failed: {str(e)}")
    except Exception as e:
        raise Exception(f"Token generation error: {str(e)}")

@app.route('/')
def index():
    """Main page with embedded Power BI report"""
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/embed-token')
def get_embed_token():
    """Generate Power BI embed token"""
    try:
        print(f"🔄 [{datetime.now().strftime('%H:%M:%S')}] Starting embed token generation...")
        
        # Step 1: Get access token
        access_token = get_access_token()
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        # Step 2: Get report details
        report_url = f"https://api.powerbi.com/v1.0/myorg/groups/{CONFIG['group_id']}/reports/{CONFIG['report_id']}"
        print(f"📊 Getting report details from: {report_url}")
        
        report_response = requests.get(report_url, headers=headers, timeout=30)
        
        if report_response.status_code != 200:
            error_msg = report_response.text
            print(f"❌ Report request failed (Status {report_response.status_code}): {error_msg}")
            raise Exception(f"Report access failed (Status {report_response.status_code}): {error_msg}")
            
        report_data = report_response.json()
        print(f"✅ Report details obtained: {report_data.get('name', 'Unnamed Report')}")
        
        # Step 3: Generate embed token
        embed_url = f"https://api.powerbi.com/v1.0/myorg/groups/{CONFIG['group_id']}/reports/{CONFIG['report_id']}/GenerateToken"
        embed_data = {
            'accessLevel': 'View',
            'allowSaveAs': False
        }
        
        print(f"🔐 Generating embed token...")
        embed_response = requests.post(embed_url, headers=headers, json=embed_data, timeout=30)
        
        if embed_response.status_code != 200:
            error_msg = embed_response.text
            print(f"❌ Embed token generation failed (Status {embed_response.status_code}): {error_msg}")
            raise Exception(f"Embed token generation failed (Status {embed_response.status_code}): {error_msg}")
            
        embed_token = embed_response.json()['token']
        print("✅ Embed token generated successfully")
        
        # Return success response
        return jsonify({
            'token': embed_token,
            'embedUrl': report_data['embedUrl'],
            'reportId': CONFIG['report_id'],
            'reportName': report_data.get('name', 'Power BI Report'),
            'success': True,
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        error_msg = str(e)
        print(f"❌ Error in embed token generation: {error_msg}")
        return jsonify({
            'error': error_msg,
            'success': False,
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/debug/test-connection')
def test_connection():
    """Debug endpoint to test Power BI API connection"""
    try:
        access_token = get_access_token()
        headers = {'Authorization': f'Bearer {access_token}'}
        
        # Test 1: List workspaces
        workspaces_response = requests.get("https://api.powerbi.com/v1.0/myorg/groups", headers=headers)
        
        # Test 2: List reports in target workspace
        reports_url = f"https://api.powerbi.com/v1.0/myorg/groups/{CONFIG['group_id']}/reports"
        reports_response = requests.get(reports_url, headers=headers)
        
        return jsonify({
            'config': {
                'workspace_id': CONFIG['group_id'],
                'report_id': CONFIG['report_id'],
                'client_id': CONFIG['client_id']
            },
            'workspaces_test': {
                'status_code': workspaces_response.status_code,
                'accessible': workspaces_response.status_code == 200,
                'data': workspaces_response.json() if workspaces_response.status_code == 200 else workspaces_response.text[:500]
            },
            'reports_test': {
                'status_code': reports_response.status_code,
                'accessible': reports_response.status_code == 200,
                'data': reports_response.json() if reports_response.status_code == 200 else reports_response.text[:500]
            }
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/health')
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'Server running',
        'config_loaded': True,
        'timestamp': datetime.now().isoformat()
    })

if __name__ == '__main__':
    print("=" * 60)
    print("🚀 STARTING EBY'S POWER BI EMBED SERVER")
    print("=" * 60)
    print(f"📊 Workspace ID: {CONFIG['group_id']}")
    print(f"📈 Report ID:    {CONFIG['report_id']}")
    print(f"🔑 Client ID:    {CONFIG['client_id']}")
    print("-" * 60)
    print("🌐 Main Application: http://localhost:8080")
    print("🔍 Debug Connection: http://localhost:8080/debug/test-connection")
    print("💓 Health Check:     http://localhost:8080/health")
    print("=" * 60)
    
    app.run(debug=True, host='0.0.0.0', port=8080)
from flask import Flask, render_template_string, jsonify
from flask_cors import CORS
import requests
import json

app = Flask(__name__)
# Update CORS to allow requests from GitHub Pages
CORS(app, origins=["https://aviation-news.github.io", "http://localhost:8080", "http://localhost:5000"])

# Your exact configuration
CONFIG = {
  'tenant_id': '65693725-b6ed-4482-838d-454f6f79eafb',
  'client_id': '8264d2da-a712-455b-bb14-6e10d4db8676',
  'client_secret': 'D128Q~XhARPUB_ddynizWtw-Mouah6GTOCkPKb-C',
  'group_id': '77200bef-3c01-4ece-b20a-10de91382e51',
  'report_id': 'df36791a-3629-4b43-b795-9ba2229f37be'
}

def get_access_token():
  """Get Azure AD access token for Power BI API"""
  token_url = f"https://login.microsoftonline.com/{CONFIG['tenant_id']}/oauth2/v2.0/token"
  token_data = {
      'grant_type': 'client_credentials',
      'client_id': CONFIG['client_id'],
      'client_secret': CONFIG['client_secret'],
      'scope': 'https://analysis.windows.net/powerbi/api/.default'
  }
  
  response = requests.post(token_url, data=token_data)
  if response.status_code == 200:
      return response.json()['access_token']
  else:
      raise Exception(f"Failed to get token: {response.text}")

def get_embed_token():
  """Get Power BI embed token and report details"""
  access_token = get_access_token()
  embed_url = f"https://api.powerbi.com/v1.0/myorg/groups/{CONFIG['group_id']}/reports/{CONFIG['report_id']}/GenerateToken"
  
  headers = {
      'Authorization': f'Bearer {access_token}',
      'Content-Type': 'application/json'
  }
  
  # Simple embed data for non-RLS datasets
  embed_data = {
      'accessLevel': 'View'
  }
  
  response = requests.post(embed_url, headers=headers, json=embed_data)
  
  if response.status_code == 200:
      embed_response = response.json()
      report_url = f"https://api.powerbi.com/v1.0/myorg/groups/{CONFIG['group_id']}/reports/{CONFIG['report_id']}"
      report_response = requests.get(report_url, headers=headers)
      
      if report_response.status_code == 200:
          report_data = report_response.json()
          return {
              'embed_token': embed_response['token'],
              'embed_url': report_data['embedUrl'],
              'report_id': CONFIG['report_id']
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
      
      # ✅ Complete HTML template with fixed JavaScript
      html_template = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Power BI Embedded Report</title>
  <script src="https://cdn.jsdelivr.net/npm/powerbi-client@2.20.1/dist/powerbi.min.js"></script>
  <style>
      body {
          font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
          margin: 0;
          padding: 20px;
          background-color: #f5f5f5;
      }
      .container {
          max-width: 1200px;
          margin: 0 auto;
          background: white;
          border-radius: 8px;
          box-shadow: 0 2px 10px rgba(0,0,0,0.1);
          overflow: hidden;
      }
      .header {
          background: #0078d4;
          color: white;
          padding: 20px;
          text-align: center;
      }
      .status {
          padding: 15px;
          margin: 15px;
          border-radius: 5px;
          font-weight: bold;
      }
      .status.loading {
          background: #fff4e6;
          color: #663c00;
          border: 1px solid #ffd700;
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
      #powerbi-container {
          width: 100%;
          height: 700px;
          border: none;
          margin: 0;
      }
      .debug-info {
          background: #f8f9fa;
          border: 1px solid #dee2e6;
          border-radius: 5px;
          padding: 15px;
          margin: 15px;
          font-family: monospace;
          font-size: 12px;
      }
  </style>
</head>
<body>
  <div class="container">
      <div class="header">
          <h1>📊 Power BI Embedded Report</h1>
          <p>Report ID: {{ report_id }}</p>
      </div>
      
      <div id="status" class="status loading">
          ⏳ Initializing Power BI report...
      </div>
      
      <div class="debug-info">
          <strong>🔧 Debug Info:</strong><br>
          • Report ID: {{ report_id }}<br>
          • Token Length: {{ embed_token|length }} characters<br>
          • Embed URL: {{ embed_url[:60] }}...<br>
      </div>
      
      <div id="powerbi-container"></div>
  </div>

  <script>
      console.log('🚀 Starting Power BI embed process...');
      
      // ✅ Configuration with your exact data
      const config = {
          type: 'report',
          id: '{{ report_id }}',
          embedUrl: '{{ embed_url }}',
          accessToken: '{{ embed_token }}',
          tokenType: 1,  // Embed token
          settings: {
              filterPaneEnabled: true,
              navContentPaneEnabled: true,
              panes: {
                  filters: {
                      expanded: false,
                      visible: true
                  },
                  pageNavigation: {
                      visible: true
                  }
              }
          }
      };
      
      console.log('📋 Config prepared:', {
          reportId: config.id,
          embedUrlPreview: config.embedUrl.substring(0, 50) + '...',
          tokenLength: config.accessToken.length,
          tokenType: config.tokenType
      });
      
      const embedContainer = document.getElementById('powerbi-container');
      const statusDiv = document.getElementById('status');
      
      // ✅ Embed the report
      try {
          console.log('🔧 Attempting to embed report...');
          const report = powerbi.embed(embedContainer, config);
          
          // Handle successful load
          report.on('loaded', () => {
              console.log('✅ Report loaded successfully');
              statusDiv.className = 'status success';
              statusDiv.innerHTML = '✅ Report loaded successfully!';
          });
          
          // Handle successful render
          report.on('rendered', () => {
              console.log('✅ Report rendered successfully');
              statusDiv.className = 'status success';
              statusDiv.innerHTML = '✅ Report is now fully rendered and interactive!';
          });
          
          // Handle errors
          report.on('error', (event) => {
              const error = event.detail;
              console.error('❌ Power BI Error:', error);
              statusDiv.className = 'status error';
              statusDiv.innerHTML = `❌ Error: ${error.message || JSON.stringify(error)}`;
          });
          
      } catch (error) {
          console.error('❌ Embed initialization failed:', error);
          statusDiv.className = 'status error';
          statusDiv.innerHTML = `❌ Embed Error: ${error.message}`;
      }
  </script>
</body>
</html>
      """
      
      return render_template_string(html_template,
                                  embed_token=embed_data['embed_token'],
                                  embed_url=embed_data['embed_url'],
                                  report_id=embed_data['report_id'])
      
  except Exception as e:
      return f"""
      <div style="padding: 20px; background: #f8d7da; color: #721c24; border-radius: 5px; margin: 20px;">
          <h2>❌ Server Error</h2>
          <p><strong>Error:</strong> {str(e)}</p>
          <p><strong>Type:</strong> {type(e).__name__}</p>
          <hr>
          <p><a href="/test" style="color: #0078d4;">🧪 Try Test Endpoint</a></p>
      </div>
      """

@app.route('/test')
def test():
  """Test endpoint to verify token generation"""
  try:
      embed_data = get_embed_token()
      return jsonify({
          'success': True,
          'message': '✅ Embed token generated successfully!',
          'data': {
              'token_length': len(embed_data['embed_token']),
              'embed_url': embed_data['embed_url'],
              'report_id': embed_data['report_id'],
              'token_preview': embed_data['embed_token'][:50] + '...'
          },
          'config': {
              'tenant_id': CONFIG['tenant_id'],
              'client_id': CONFIG['client_id'],
              'group_id': CONFIG['group_id'],
              'report_id': CONFIG['report_id']
          }
      })
  except Exception as e:
      return jsonify({
          'success': False,
          'error': str(e),
          'error_type': type(e).__name__
      }), 500

# New endpoint specifically for GitHub Pages integration
@app.route('/get-embed-token')
def get_embed_token_endpoint():
  """API endpoint to get embed token - specifically for GitHub Pages"""
  try:
      embed_data = get_embed_token()
      return jsonify({
          'success': True,
          'reportId': embed_data['report_id'],
          'embedUrl': embed_data['embed_url'],
          'token': embed_data['embed_token']
      })
  except Exception as e:
      return jsonify({
          'success': False,
          'error': str(e)
      }), 500

@app.route('/health')
def health():
  """Health check endpoint"""
  return jsonify({
      'status': 'healthy',
      'app': 'Power BI Embedded Demo',
      'port': 8080,
      'endpoints': {
          'main': 'http://localhost:8080/',
          'test': 'http://localhost:8080/test',
          'health': 'http://localhost:8080/health',
          'token': 'http://localhost:8080/get-embed-token'
      }
  })

if __name__ == '__main__':
  print("=" * 50)
  print("🚀 Power BI Embedded Demo Starting...")
  print("=" * 50)
  print(f"📊 Main App: http://localhost:8080/")
  print(f"🧪 Test API: http://localhost:8080/test")
  print(f"❤️  Health:   http://localhost:8080/health")
  print(f"🔑 Token API: http://localhost:8080/get-embed-token")
  print("=" * 50)
  print(f"🔧 Config:")
  print(f"   • Tenant:  {CONFIG['tenant_id']}")
  print(f"   • Group:   {CONFIG['group_id']}")  
  print(f"   • Report:  {CONFIG['report_id']}")
  print("=" * 50)
  
  app.run(host='0.0.0.0', port=8080, debug=True)
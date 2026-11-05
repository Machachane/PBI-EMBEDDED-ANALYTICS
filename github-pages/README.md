# Power BI Embedded GitHub Pages Demo

This folder contains a static HTML page that can be hosted on GitHub Pages to demonstrate connecting to your Power BI Embedded Flask backend.

## ⚠️ Important Limitations

GitHub Pages can only host static content and cannot:
- Run Python/Flask code
- Make direct API calls to Power BI (CORS restrictions)
- Store sensitive credentials

For this reason, this demo page can only:
1. Test connectivity to your Flask backend (which must be running separately)
2. Display diagnostic information
3. Explain how to properly deploy the full solution

## 🚀 Deployment Options

### Option 1: Development Testing

1. Run your Flask backend locally:
   ```bash
   python app.py
   ```
   This will start your backend on http://localhost:8080

2. Host this static page on GitHub Pages or open it locally in a browser.

3. In the page, enter your local backend URL (http://localhost:8080) and click "Connect".

### Option 2: Cloud Deployment (Recommended)

For a full production deployment:

1. **Deploy the Flask Backend** to a hosting service:
   - [Heroku](https://www.heroku.com/) (Free tier available)
   - [Azure App Service](https://azure.microsoft.com/en-us/services/app-service/) (Free tier available)
   - [Google Cloud Run](https://cloud.google.com/run) (Free tier available)
   - [AWS Elastic Beanstalk](https://aws.amazon.com/elasticbeanstalk/) (Free tier may be available)

2. **Update CORS settings** in your Flask app to allow requests from your GitHub Pages domain:
   ```python
   # In app.py
   CORS(app, origins=["https://yourusername.github.io"])
   ```

3. **Use environment variables** for sensitive configuration in your deployed backend:
   ```python
   # In app.py on your hosting service
   import os
   
   CONFIG = {
     'tenant_id': os.environ.get('TENANT_ID'),
     'client_id': os.environ.get('CLIENT_ID'),
     'client_secret': os.environ.get('CLIENT_SECRET'),
     'group_id': os.environ.get('GROUP_ID'),
     'report_id': os.environ.get('REPORT_ID')
   }
   ```

4. **Create a dedicated page** that directly embeds your report by pointing to your deployed backend.

## 📋 Setup Instructions for GitHub Pages

1. Push this folder to your GitHub repository:
   ```bash
   git add github-pages
   git commit -m "Add GitHub Pages demo for Power BI Embedded"
   git push
   ```

2. Go to your GitHub repository → Settings → Pages
   - Source: select "main" branch and "/docs" folder
   - Click "Save"

3. Rename this folder to "docs" if you want to use the default GitHub Pages setup:
   ```bash
   git mv github-pages docs
   git commit -m "Rename folder for GitHub Pages"
   git push
   ```

4. Your page will be available at: `https://[username].github.io/[repository-name]/`

## 🔐 Security Best Practices

- Never include API keys, client secrets, or tokens in code hosted on GitHub Pages
- Always use HTTPS for all communications between components
- Set appropriate CORS headers on your backend to limit which origins can access it
- Consider implementing additional authentication for your backend API endpoints
- Regularly rotate credentials used in your Azure AD application

## 🔧 Troubleshooting

If you experience issues:

1. Check that your Flask backend is running and accessible
2. Verify CORS is properly configured on your backend
3. Check browser console for JavaScript errors
4. Ensure your Azure AD application has the proper permissions
5. Verify your Power BI workspace and report settings allow embedding
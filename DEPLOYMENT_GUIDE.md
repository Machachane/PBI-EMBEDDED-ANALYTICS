# Deployment Guide: Power BI Embedded on GitHub Pages

This guide will walk you through deploying your Power BI Embedded application using GitHub Pages and your Flask backend.

## Overview

This deployment consists of two parts:
1. **GitHub Pages** - Hosts the static frontend (HTML, CSS, JS)
2. **Flask Backend** - Handles authentication and token generation (must be hosted separately)

## Step 1: Set Up Your Repository

I've created the following files for you:

1. `index.html` - The main GitHub Pages entry point
2. `app_with_cors.py` - Updated Flask backend with CORS support and a new endpoint
3. `DEPLOYMENT_GUIDE.md` - This guide

## Step 2: Deploy to GitHub Pages

1. **Commit and push** the new files to your GitHub repository:
   ```bash
   git add index.html app_with_cors.py DEPLOYMENT_GUIDE.md
   git commit -m "Add GitHub Pages integration for Power BI Embedded"
   git push
   ```

2. **Enable GitHub Pages** in your repository settings:
   - Go to https://github.com/Aviation-News/pbi_implementation/settings/pages
   - Under "Source", select "Deploy from a branch"
   - Choose "main" branch and "/ (root)" folder
   - Click "Save"

3. **Wait for deployment** to complete (usually takes a few minutes)
   - Your site will be available at: https://aviation-news.github.io/pbi_implementation/

## Step 3: Run Your Flask Backend

You have two options for running the backend:

### Option A: Local Development

1. Run the updated Flask app with CORS support:
   ```bash
   python app_with_cors.py
   ```
   This will start your backend on http://localhost:8080

2. When using the GitHub Pages frontend:
   - Enter `http://localhost:8080` as your backend URL
   - Click "Connect to Backend"

### Option B: Cloud Deployment (Recommended for Production)

1. **Choose a hosting platform** for your Flask app:
   - [Heroku](https://www.heroku.com/)
   - [Azure App Service](https://azure.microsoft.com/en-us/services/app-service/)
   - [Google Cloud Run](https://cloud.google.com/run)
   - [AWS Elastic Beanstalk](https://aws.amazon.com/elasticbeanstalk/)

2. **Secure your credentials** by using environment variables:
   ```python
   # Example for Heroku
   import os
   
   CONFIG = {
     'tenant_id': os.environ.get('TENANT_ID'),
     'client_id': os.environ.get('CLIENT_ID'),
     'client_secret': os.environ.get('CLIENT_SECRET'),
     'group_id': os.environ.get('GROUP_ID'),
     'report_id': os.environ.get('REPORT_ID')
   }
   ```

3. **Deploy your Flask app** following the hosting platform's instructions

4. **When using the GitHub Pages frontend**:
   - Enter your cloud backend URL (e.g., `https://your-app.herokuapp.com`)
   - Click "Connect to Backend"

## How It Works

The architecture works as follows:

1. **User visits** your GitHub Pages site
2. **User connects** to your backend by entering the backend URL
3. **Backend authenticates** with Azure AD and generates an embed token
4. **Frontend receives** the token and embeds the Power BI report
5. **User interacts** with the embedded report

## Troubleshooting

### CORS Issues

If you see CORS errors in the browser console:

1. Ensure your backend is running the `app_with_cors.py` version
2. Verify that your GitHub Pages origin is allowed in the CORS configuration
3. Make sure you're using HTTPS for both the frontend and backend in production

### Authentication Errors

If you see authentication errors:

1. Check that your Azure AD credentials are correct
2. Verify your Power BI workspace and report permissions
3. Ensure your Azure AD application has the proper API permissions

### Connection Refused

If you can't connect to your backend:

1. Make sure your Flask app is running
2. Check that the URL you entered is correct
3. If using localhost, ensure you're accessing GitHub Pages from the same machine

## Security Considerations

1. **Never expose** client secrets in GitHub repositories
2. **Always use** HTTPS for production deployments
3. **Set up** proper CORS restrictions in production
4. **Store** sensitive credentials as environment variables
5. **Consider** implementing additional authentication for your backend API

## Need More Help?

- Check the [Power BI Embedded documentation](https://docs.microsoft.com/en-us/power-bi/developer/embedded/)
- Review the [GitHub Pages documentation](https://docs.github.com/en/pages)
- Consult the [Flask deployment guides](https://flask.palletsprojects.com/en/2.0.x/deploying/)
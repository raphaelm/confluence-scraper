Confluence Scraper
==================

Downloads all pages and attachments from a Confluence Cloud instance for backup purposes.

Setup
-----

* Create a new OAuth 2.0 app in the [Atlassian Developer Console](https://developer.atlassian.com/console/myapps/)

* Under permissions, request these scopes (not sure if all are really necessary): ``read:template:confluence read:space:confluence read:space-details:confluence read:relation:confluence read:custom-content:confluence read:content.metadata:confluence read:content:confluence read:content-details:confluence read:comment:confluence read:attachment:confluence read:content.property:confluence read:page:confluence read:label:confluence``.
  Set a redirect URL that does not really need to exist.

* Run ``pip install -Ur requirements.txt``

* Create a new file `conf.py` with content like this:

```
CLIENT_ID = "…"
CLIENT_SECRET = "…"
CALLBACK_URL = "https://confluence-scraper.rami.io"
DATA_FOLDER = "data"
MAX_ATTACHMENT_SIZE = 1024 * 1024 * 1024  # 1 GB
```

* Run ``python main.py auth``, click the link in the output, and paste the redirect URL from your browser after the authentication is done.
  This is probably required every 365 days, or more often if the script is not run regularly.

* Run ``python main.py download`` to start the download.

Known issues & limitations
--------------------------

* The table of contents will be entirely out of order, since the confluence API does not expose
  the order of pages.
  
* Macros are not rendered, but their content is in some cases. For example, the "Info" macro looks fine,
  while the "draw.io" macro does not render anything. However, draw.io diagrams are preserved through
  a list of attachments.
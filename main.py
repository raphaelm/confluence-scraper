import json
import logging
import os
import secrets
import time
from collections import defaultdict
from datetime import timezone, datetime, timedelta
from urllib.parse import urlencode, urlparse, parse_qs, unquote

import click
import requests
from bs4 import BeautifulSoup
from dateutil.parser import parse

import conf
from conf import CLIENT_ID, CLIENT_SECRET, CALLBACK_URL, DATA_FOLDER

logging.basicConfig(level=logging.INFO)


@click.group()
def cli():
    if not os.path.exists(DATA_FOLDER):
        os.mkdir(DATA_FOLDER)
    pass


@cli.command()
def auth():
    state = secrets.token_urlsafe(24)
    auth_url = 'https://auth.atlassian.com/authorize?' + urlencode({
        'audience': 'api.atlassian.com',
        'client_id': CLIENT_ID,
        'scope': 'offline_access read:template:confluence read:space:confluence read:space-details:confluence read:relation:confluence read:custom-content:confluence read:content.metadata:confluence read:content:confluence read:content-details:confluence read:comment:confluence read:attachment:confluence read:content.property:confluence read:page:confluence read:label:confluence',
        'redirect_uri': CALLBACK_URL,
        'state': state,
        'response_type': 'code',
        'prompt': 'consent',
    })
    click.echo('Please head with your browser to')
    click.echo(auth_url)
    return_url = click.prompt('Please paste the URL you have been redirected to', type=str)
    return_url = urlparse(return_url)
    return_params = parse_qs(return_url.query)

    if return_params['state'][0] != state:
        click.echo('Invalid state parameter')
        return

    r = requests.post(
        'https://auth.atlassian.com/oauth/token',
        json={
            'grant_type': 'authorization_code',
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET,
            'code': return_params['code'][0],
            'redirect_uri': CALLBACK_URL,
        }
    )
    r.raise_for_status()
    access_token = r.json()['access_token']
    refresh_token = r.json()['refresh_token']

    r = requests.get(
        'https://api.atlassian.com/oauth/token/accessible-resources',
        headers={
            'Authorization': f'Bearer {access_token}'
        }
    )
    cloudid = r.json()[0]['id']
    with open(os.path.join(DATA_FOLDER, 'auth.json'), 'w') as f:
        json.dump({
            'access_token': access_token,
            'refresh_token': refresh_token,
            'cloudid': cloudid,
        }, f)
    click.echo('OK!')


def _refresh_token():
    with open(os.path.join(DATA_FOLDER, 'auth.json'), 'r') as f:
        auth = json.load(f)

    r = requests.post(
        'https://auth.atlassian.com/oauth/token',
        json={
            'grant_type': 'refresh_token',
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET,
            'refresh_token': auth['refresh_token'],
            'redirect_uri': CALLBACK_URL,
        }
    )
    r.raise_for_status()
    auth['access_token'] = r.json()['access_token']
    auth['refresh_token'] = r.json()['refresh_token']
    with open(os.path.join(DATA_FOLDER, 'auth.json'), 'w') as f:
        json.dump(auth, f)
    return auth


def _iterate_paged_list(session, cloudid, url):
    r = session.get(f'https://api.atlassian.com/ex/confluence/{cloudid}{url}')
    r.raise_for_status()
    d = r.json()
    yield from d['results']
    while d['_links'].get('next'):
        r = session.get(f'https://api.atlassian.com/ex/confluence/{cloudid}{d["_links"].get("next")}')
        r.raise_for_status()
        d = r.json()
        yield from d['results']
        time.sleep(.5)


def _storage_path(webui_url):
    if '/pages/' in webui_url or '/overview' in webui_url:
        webui_url += '.html'
    dirname = os.path.dirname(DATA_FOLDER + webui_url)
    if not os.path.exists(dirname):
        os.makedirs(dirname, exist_ok=True)
    return os.path.join(dirname, unquote(os.path.basename(webui_url)))


def _process_page(spacekey, content, attachments):
    html = content['body']['styled_view']['value']
    soup = BeautifulSoup(html, "lxml")

    path_to_data = '../' * (content['_links']['webui'].count('/') - 1)

    for a in soup.find_all('a'):
        if a.attrs.get('href') and a.attrs['href'].startswith('/wiki/'):
            a.attrs['href'] += '.html'
            a.attrs['href'] = a.attrs['href'].replace('/wiki/', path_to_data)

    for img in soup.find_all('img'):
        if img.attrs.get('data-emoji-fallback'):
            img.name = 'span'
            img.append(img.attrs.get('data-emoji-fallback'))
            img.attrs = {}
            continue
        if img.attrs.get('src') and '/thumbnails/' in img.attrs['src']:
            # file:///home/raphael/proj/confluence-scraper/data/download/attachments/46760067/crewpit_logo_large.ai?version=2&modificationDate=1578330838272&cacheVersion=1&api=v2
            img.attrs['src'] = path_to_data + 'download/attachments/' + img.attrs['src'].split('/thumbnails/')[1]
            if 'srcset' in img.attrs:
                del img.attrs['srcset']

    for t in soup.find_all('title'):
        t.decompose()
    for t in soup.find_all('base'):
        t.decompose()

    breadcrumbs = [
        f'<a href="{path_to_data}spaces/{spacekey}/index.html">{spacekey}</a>'
    ]
    for parent in content['ancestors']:
        breadcrumbs.append(f'<a href="{path_to_data}{parent["_links"]["webui"].strip("/")}.html">{parent["title"]}</a>')
    breadcrumbs = " &gt; ".join(breadcrumbs)

    if attachments:
        attachments = '<hr><h2>Attachments</h2><ul>' + ''.join(
            f'<li><a href="{path_to_data}{unquote(url)}">{title}</a></li>' for title, url in attachments
        ) + '</ul>'
    else:
        attachments = ''

    body = str(soup)
    return f"""
    <html>
        <head>
            <title>{content['title']}</title>
            <meta charset="utf-8">
            <style>
                [data-macro-name] {{
                    min-height: 20px;
                    border: 1px solid red;
                    padding: 5px;
                }}
                [data-macro-name]::before {{
                    content: "Macro";
                    font-family: monospace;
                    color: red;
                }}
            </style>
        </head>
        <body>
            {breadcrumbs}
            <h1>{content['title']}</h1>
            {body}
            {attachments}
        </body>
    </html>
    """


def _build_toc(children, node):
    items = []
    if node not in children:
        return ""
    for nodeid, title, link in children[node]:
        items.append(f"<li><a href='{link}'>{title}</a>{_build_toc(children, nodeid)}</li>")
    return f'<ul>{"".join(items)}</ul>'


def _write_toc(path, children):
    with open(path, 'w') as f:
        l = _build_toc(children, None)
        f.write(f"""
    <html>
        <head>
            <title>Content</title>
            <meta charset="utf-8">
        </head>
        <body>
            <h1>Content</h1>
            {l}
        </body>
    </html>
    """)


@cli.command()
@click.option('--space', help='Space key.')
def download(space):
    auth = _refresh_token()
    cloudid = auth['cloudid']
    with requests.Session() as session:
        session.headers['Authorization'] = f'Bearer {auth["access_token"]}'
        if space:
            spaces = [space]
        else:
            spaces = [s['key'] for s in _iterate_paged_list(session, cloudid, '/rest/api/space')]
        for spacekey in spaces:
            logging.info(f'Downloading space {spacekey}')

            children = defaultdict(list)
            for content in _iterate_paged_list(session, cloudid,
                                               f'/rest/api/content?{urlencode({"spaceKey": spacekey, "expand": "body.styled_view,ancestors"})}'):
                if content['status'] != 'archived':
                    parent = content['ancestors'][-1]['id'] if content['ancestors'] else None
                    children[parent].append((
                        content['id'],
                        content['title'],
                        content['_links']['webui'].replace(f'/spaces/{spacekey}/', '') + '.html',
                    ))

                logging.info(f"Downloading page {spacekey}/{content['title']} ({content['status']})")
                attachments = []
                for attachment in _iterate_paged_list(session, cloudid,
                                                      f'/rest/api/content/{content["id"]}/child/attachment?expand=history.lastUpdated'):
                    storage_path = _storage_path(urlparse(attachment['_links']['download']).path)
                    attachments.append((
                        attachment['title'],
                        urlparse(attachment['_links']['download']).path,
                    ))

                    if os.path.exists(storage_path):
                        # simplistic version check
                        lastUpdate = parse(attachment['history']['lastUpdated']['when'])
                        lastDownload = datetime.fromtimestamp(os.stat(storage_path).st_mtime, tz=timezone.utc)
                        if lastDownload > lastUpdate + timedelta(minutes=30):
                            continue

                    if attachment['extensions']['fileSize'] > conf.MAX_ATTACHMENT_SIZE:
                        logging.warning(f"Skipping attachment {attachment['title']} on page {spacekey}/{content['title']} because it is larger than the maximum size")
                        continue

                    logging.debug(f"Downloading attachment {attachment['title']}")
                    with open(storage_path, 'wb') as f:
                        r = session.get(
                            f'https://api.atlassian.com/ex/confluence/{cloudid}/rest/api/content/{content["id"]}/child/attachment/{attachment["id"]}/download')
                        try:
                            r.raise_for_status()
                        except:
                            logging.warning(f"Could not download attachment {attachment['title']} on page {spacekey}/{content['title']}")
                        else:
                            for chunk in r.iter_content(chunk_size=512 * 1024):
                                if chunk:  # filter out keep-alive new chunks
                                    f.write(chunk)
                    time.sleep(.5)

                with open(_storage_path(content['_links']['webui']), 'w') as f:
                    f.write(_process_page(spacekey, content, attachments))

            _write_toc(_storage_path(f'/spaces/{spacekey}/index.html'), children)


if __name__ == '__main__':
    cli()

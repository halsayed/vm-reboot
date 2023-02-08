import requests
import click
import logging
import urllib3
from datetime import datetime, timedelta
import pytz
import json


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
log = logging.getLogger('vm-reboot')


def save_json(data, filename):
    with open(filename, 'w') as f:
        json.dump(data, f, indent=4)


class PrismClient(requests.Session):
    def __init__(self):
        self.host = None
        self.port = 9440
        self.user = None
        self.password = None
        self.auth = None
        self.timezone = "UTC"
        self.base_url = None
        super().__init__()

    def init(self, host, port, user, password, verify=False):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.auth = (self.user, self.password)
        self.verify = verify
        self.timezone = timezone
        self.base_url = "https://{}:{}/api/nutanix/v3/".format(self.host, self.port)

    def authenticate(self):
        r = self.get(self.base_url + "users/me")
        if r.status_code == 200:
            return True
        else:
            return False

    def list_clusters(self):
        payload = {'kind': 'cluster'}
        r = self.post(self.base_url + 'clusters/list', json=payload)
        if r.status_code == 200:
            log.info('Clusters listed successfully')
            log.info('Total clusters: {}'.format(len(r.json()['entities'])))
            log.debug('Clusters: {}'.format(r.json()['entities']))
            clusters = []
            for cluster in r.json()['entities']:
                clusters.append({'name': cluster['status']['name'],
                                 'uuid': cluster['metadata']['uuid']})
            return clusters
        else:
            log.error('Error listing clusters: {}'.format(r.text))
            return None

    def list_vms(self, cluster_uuid, category=None):
        search_value = f'(platform_type!=aws,platform_type==[no_val]);is_cvm==0;power_state==on;cluster=={cluster_uuid}'
        if category:
            category_key = category.split(':')[0]
            category_value = category.split(':')[1]
            search_value += f';(category_name=={category_key};category_value=={category_value})'


prism_client = PrismClient()
timezone = pytz.utc
output_file = None


@click.group()
@click.option('--username', '-u', prompt=True, help='Username for Prism Central')
@click.option('--password', '-p', prompt=True, hide_input=True, help='Password for Prism Central')
@click.option('--prism', '-pc', prompt=True, help='Prism Central IP or FQDN')
@click.option('--verify', '-v', default=False, help='Verify SSL certificate (default: False)')
@click.option('--port', '-port', default=9440, help='Prism Central Port (default: 9440)')
@click.option('--debug', '-d', default=False, help='Debug mode (default: False)')
@click.option('--logs_tz', '-tz', default='UTC', help='Logs timezone (default: UTC)')
@click.option('--output', '-o', default=None, help='Output filename')
def main(username, password, prism, verify, port, debug, logs_tz, output):

    # configure logging
    global log
    if debug:
        log.setLevel(logging.DEBUG)
        log_level = logging.DEBUG
    else:
        log.setLevel(logging.INFO)
        log_level = logging.INFO

    ch = logging.StreamHandler()
    ch.setLevel(log_level)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    ch.setFormatter(formatter)
    log.addHandler(ch)

    # configure prism client and test authentication
    prism_client.init(prism, port, username, password, verify)
    if prism_client.authenticate():
        log.info('Authentication successful')
    else:
        log.error('Authentication failed')
        exit(1)

    # configure timezone
    global timezone
    timezone = pytz.timezone(logs_tz)
    log.info('Timezone set to {}'.format(timezone))

    # configure filename
    global output_file
    if output:
        output_file = output
    else:
        output_file = f'logs-{int(datetime.now(timezone).timestamp()*1000000)}.json'


@main.command()
def list_clusters():
    log.info('Listing clusters registered in Prism Central')
    clusters = prism_client.list_clusters()
    i = 1
    for cluster in clusters:
        print(f'{i}. {cluster["name"]} ({cluster["uuid"]})')
        i += 1


if __name__ == '__main__':
    main()
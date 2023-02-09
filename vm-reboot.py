import requests
import click
import logging
import urllib3
from datetime import datetime, timedelta
import pytz
import json
from tabulate import tabulate


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
log = logging.getLogger(__name__)


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
            log.debug('Clusters: {}'.format(r.json()['entities']))
            clusters = []
            counter = 1
            for cluster in r.json()['entities']:
                if 'PRISM_CENTRAL' in cluster['status']['resources']['config']['service_list']:
                    log.debug(f"Skipping Prism Central cluster: {cluster['status']['name']}")
                    continue

                clusters.append({'no': counter, 'cluster_name': cluster['status']['name'],
                                 'uuid': cluster['metadata']['uuid']})
                counter += 1

            log.info(f'Total clusters: {counter-1}')
            log.debug('Clusters: {}'.format(clusters))
            return clusters
        else:
            log.error('Error listing clusters: {}'.format(r.text))
            return None

    def list_vms(self, cluster_uuid, category=None):
        search_value = f'(platform_type!=aws,platform_type==[no_val]);is_cvm==0;power_state==on;cluster=={cluster_uuid}'
        search_value += ';vm_type==kGuestVM'
        if category:
            try:
                category_key = category.split(':')[0]
                category_value = category.split(':')[1]
                search_value += f';(category_name=={category_key};category_value=={category_value})'
            except IndexError:
                log.error('Category value or key is empty')
                return None

        log.debug('Search value: {}'.format(search_value))
        payload = {
            'entity_type': 'mh_vm',
            'query_name': f'eb:data-{int(datetime.timestamp(datetime.now()))}',
            'group_member_sort_attribute': 'vm_name',
            'group_member_sort_order': 'ASCENDING',
            'group_member_attributes': [{'attribute': 'vm_name'}, {'attribute': 'categories'}],
            'filter_criteria': search_value
        }
        log.debug('Payload: {}'.format(payload))
        r = self.post(self.base_url + 'groups', json=payload)
        if r.status_code == 200:
            log.info('VMs listed successfully')
            result_count = int(r.json().get('filtered_entity_count'))
            log.info(f'Total VMs: {result_count}')
            log.debug(f'VMs: {r.json()}')
            if not result_count:
                log.info('No VMs found')
                return None
            vms = []
            counter = 1
            for vm in r.json()['group_results'][0]['entity_results']:
                vm_name = None
                vm_categories = None
                for data in vm['data']:
                    if data['name'] == 'vm_name':
                        vm_name = data['values'][0]['values'][0]
                    if data['name'] == 'categories':
                        vm_categories = data['values'][0]['values']

                if vm_name:
                    vms.append({'no': counter,
                                'vm_name': vm_name,
                                'uuid': vm['entity_id'],
                                'categories': vm_categories})
                    counter += 1
            log.debug('VMs: {}'.format(vms))
            return vms
        else:
            log.error('Error listing VMs: {}'.format(r.text))
            return None


prism_client = PrismClient()
timezone = pytz.utc
output_file = None


@click.group()
@click.option('--username', '-u', prompt=True, help='Username for Prism Central')
@click.option('--password', '-p', prompt=True, hide_input=True, help='Password for Prism Central')
@click.option('--prism', '-pc', prompt=True, help='Prism Central IP or FQDN')
@click.option('--verify', '-v', is_flag=True, default=False, help='Verify SSL certificate (default: False)')
@click.option('--port', '-port', default=9440, help='Prism Central Port (default: 9440)')
@click.option('--debug', '-d', is_flag=True, default=False, help='Debug mode (default: False)')
@click.option('--logs_tz', '-tz', default='UTC', help='Logs timezone (default: UTC)')
@click.option('--disable-console', '-dc', is_flag=True, default=False, help='Disable logging to console (default: False)')
@click.option('--output', '-o', default='vm-reboot.log', help='Log output filename (default: vm-reboot.log)')
def main(username, password, prism, verify, port, debug, logs_tz, disable_console, output):

    # configure logging
    global log

    file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    console_formatter = logging.Formatter('%(message)s')

    # select logging level for console
    if debug:
        log.setLevel(logging.DEBUG)
        log_level = logging.DEBUG
    else:
        log.setLevel(logging.INFO)
        log_level = logging.INFO

    # file logging settings
    file_handler = logging.FileHandler(output)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(file_formatter)
    log.addHandler(file_handler)

    # console logging settings
    if not disable_console:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_level)
        console_handler.setFormatter(console_formatter)
        log.addHandler(console_handler)

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
    print(tabulate(clusters, headers='keys', tablefmt='psql'))


@main.command()
@click.option('--cluster', '-c', prompt=True, help='Cluster UUID')
@click.option('--category', '-cat', default=None, help='Category filter (key:value)')
def list_vms(cluster, category):
    log.info('Listing VMs in cluster {}'.format(cluster))
    vms = prism_client.list_vms(cluster, category)
    print(tabulate(vms, headers='keys', tablefmt='psql'))


if __name__ == '__main__':
    main()

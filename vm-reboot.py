import requests
import click
import logging
import urllib3
from datetime import datetime, timedelta
import pytz
import json
from tabulate import tabulate
from time import sleep


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

    def vm_poweroff(self, vm_uuid):
        r = self.get(self.base_url + f'vms/{vm_uuid}')
        payload = r.json()
        del(payload['status'])
        payload['spec']['resources']['power_state'] = 'OFF'
        r = self.put(self.base_url + f'vms/{vm_uuid}', json=payload)
        log.debug('Response: status code: {}, msg: {}'.format(r.status_code, r.text))
        if r.status_code == 202:
            log.debug(f'VM {vm_uuid} powered off successfully')
            return True
        else:
            log.error(f'Error powering off VM: {vm_uuid}, msg: {r.text}')
            return False

    def vm_check_poweroff(self, vm_uuid):
        r = self.get(self.base_url + f'vms/{vm_uuid}')
        if r.status_code == 200:
            if r.json()['status']['resources']['power_state'] == 'OFF':
                log.debug(f'VM {vm_uuid} is powered off')
                return True
            else:
                log.debug(f'VM {vm_uuid} is powered on')
                return False
        else:
            log.error(f'Error checking VM power state: {vm_uuid}, msg: {r.text}')
            return False

    def vm_poweron(self, vm_uuid):
        r = self.get(self.base_url + f'vms/{vm_uuid}')
        payload = r.json()
        del(payload['status'])
        payload['spec']['resources']['power_state'] = 'ON'
        r = self.put(self.base_url + f'vms/{vm_uuid}', json=payload)
        log.debug('Response: status code: {}, msg: {}'.format(r.status_code, r.text))
        if r.status_code == 202:
            log.debug(f'VM {vm_uuid} powered on successfully')
            return True
        else:
            log.error(f'Error powering on VM: {vm_uuid}, msg: {r.text}')
            return False

    def reboot_vms(self, vms, wait_time=2, max_retries=10):
        log.info('Starting batch VM reboot')
        poweroff_vms = []
        for vm in vms:
            log.debug(f'Powering off VM: {vm["vm_name"]}')
            if self.vm_poweroff(vm['uuid']):
                poweroff_vms.append(vm)
            else:
                log.error(f'Error powering off VM: {vm["vm_name"]}, uuid: {vm["uuid"]}')

        log.info(f'Waiting for {len(poweroff_vms)} VMs to power off')
        sleep(wait_time)
        for vm in poweroff_vms:
            retries = 0
            while retries < max_retries:
                if self.vm_check_poweroff(vm['uuid']):
                    log.debug(f'VM {vm["vm_name"]} powered off')
                    break
                else:
                    log.debug(f'VM {vm["vm_name"]} still powered on, retrying')
                    retries += 1
                    sleep(wait_time)
            else:
                log.error(f'VM {vm["vm_name"]} failed to power off')

        log.info(f'Powering on {len(poweroff_vms)} VMs')
        for vm in poweroff_vms:
            if self.vm_poweron(vm['uuid']):
                log.debug(f'VM {vm["vm_name"]} powered on')
            else:
                log.error(f'Error powering on VM: {vm["vm_name"]}, uuid: {vm["uuid"]}')
        log.info(f'Batch VM reboot completed')


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
    file_handler.setLevel(log_level)
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


@main.command()
@click.option('--cluster', '-c', prompt=True, help='Cluster UUID')
@click.option('--category', '-cat', default=None, help='Category filter (key:value)')
@click.option('--batch', '-b', default=25, help='Batch size (default: 25)')
def reboot_vms(cluster, category, batch):
    log.info('Listing VMs in cluster {}'.format(cluster))
    vms = prism_client.list_vms(cluster, category)
    print(tabulate(vms, headers='keys', tablefmt='psql'))
    log.info('Rebooting VMs in cluster {}'.format(cluster))
    log.info('Warning: This will reboot all VMs in the cluster, stop the script if you don\'t want to reboot all VMs')
    sleep(5)
    log.info('Starting reboot process')
    log.info('Batch size: {}'.format(batch))
    log.info('Total VMs: {}'.format(len(vms)))
    for i in range(0, len(vms), batch):
        if i+batch > len(vms):
            batch_size = len(vms) - i
        else:
            batch_size = batch
        log.info('Rebooting VMs {} to {}'.format(i+1, i+batch_size))
        prism_client.reboot_vms(vms[i:i+batch_size])
        log.info('Waiting 5 seconds before next batch')
        sleep(4)
    log.info('Reboot process completed')


if __name__ == '__main__':
    main()

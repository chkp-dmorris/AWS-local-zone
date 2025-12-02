#!/usr/bin/env python3

#   Copyright 2018 Check Point Software Technologies LTD

import datetime
import email.utils as eut
import json
import os
import re
import socket
import subprocess
import sys
import traceback
import filecmp

import aws_ha_mode as mode
from aws_had import update_cross_az_cluster_map, get_all_allocation_ids
from aws_ha_globals import AWSproperties, CROSS_AZ_CLUSTER_SEC_IP_MAP, CROSS_AZ_CLUSTER_SEC_IP_MAP_TEST, IFS, ACTIVE, \
    STANDBY, INTERNAL, TYPE, AWS_HA_CLI_COMMAND
import aws

if sys.version_info < (3,):
    from urllib import urlencode
    from urlparse import urlparse
else:
    from urllib.parse import urlencode, urlparse


def is_aws():
    """#TODO fixDocString"""
    return os.path.isfile('/etc/in-aws')


def log(msg):
    """#TODO fixDocString"""
    sys.stderr.write(msg)


http_proxy = urlparse(os.environ.get('http_proxy'))
proxy_address = http_proxy.hostname or ''
proxy_port = str(http_proxy.port or '')

META_DATA = 'http://169.254.169.254/2014-02-25/meta-data'

if proxy_address != '' and proxy_port.isdigit():
    HTTP_PROXY = proxy_address + ':' + proxy_port
    if not os.path.exists('/opt/CPsuite-R77'):
        subprocess.call('fw ctl set int fw_os_proxy_port ' + proxy_port,
                        shell=True)
else:
    HTTP_PROXY = None
    if not os.path.exists('/opt/CPsuite-R77'):
        subprocess.call('fw ctl set int fw_os_proxy_port 0', shell=True)


def get(url, proxy=None):
    """#TODO fixDocString"""
    cmd = ['curl_cli', '--request', 'PUT',
           'http://169.254.169.254/latest/api/token',
           '--header', 'X-aws-ec2-metadata-token-ttl-seconds: 60']
    token = subprocess.check_output(cmd)
    if not token:
        raise Exception('Failed to get metadata token\n')
    cmd = ['curl_cli', '-s', '-f', '-g', '-L']
    if proxy:
        cmd.extend(['--proxy', proxy])
    cmd.extend(['--header', 'X-aws-ec2-metadata-token: ' + token.decode('utf-8'), url])
    text = subprocess.check_output(cmd)
    try:
        return text.decode('utf-8')
    except Exception:
        return text


def test():
    """#TODO fixDocString"""
    if not is_aws():
        raise Exception('This does not look like an AWS environment\n')

    log('\nTesting if DNS is configured...\n')
    try:
        dns = subprocess.check_output(
            ['/bin/clish', '-c', 'show dns primary']).decode('utf-8').strip()
    except Exception:
        traceback.print_exc()
        raise
    match = re.search(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})', dns)
    if not match:
        raise Exception('Primary DNS server is not configured\n')
    log('Primary DNS server is: %s\n' % match.group(1))

    log('\nTesting if DNS is working...\n')
    try:
        socket.gethostbyname('s3.amazonaws.com')
        log('DNS resolving test was successful\n')
    except Exception:
        raise Exception('Failed in DNS resolving test\n')

    log('\nTesting metadata connectivity...\n')
    try:
        az = get(META_DATA + '/placement/availability-zone').strip()
        # Always use the region only (strip everything after the second dash)
        region = '-'.join(az.split('-')[:3])
        mac = get(
            META_DATA + '/network/interfaces/macs/').split('\n')[0].strip()
        vpc_id = get(META_DATA + '/network/interfaces/macs/' + mac +
                     '/vpc-id').strip()
        domain = get(META_DATA + '/services/domain')
    except Exception:
        traceback.print_exc()
        raise Exception('''Failed in metadata connectivity test
Verify that outgoing connections over TCP port 80 (HTTP) to 169.254.169.254 are
allowed by the firewall security policy.
See:
http://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-instance-metadata.html
''')

    log('Region : %s\n' % region)
    log('VPC    : %s\n' % vpc_id)
    log('Domain : %s\n' % domain)

    log('\nTesting for IAM role...\n')
    try:
        role = get(META_DATA + '/iam/security-credentials/').split(
            '\n')[0].strip()
    except Exception:
        traceback.print_exc()
        raise Exception('''Failed to retrieve IAM role
Please consult sk104418
''')
    log('Role: %s\n' % role)

    log('\nTesting for IAM credentials...\n')
    try:
        json.loads(get(META_DATA + '/iam/security-credentials/' + role))
    except Exception:
        traceback.print_exc()
        raise Exception('''Failed to retrieve IAM credentials
Please consult sk104418
''')
    log('IAM credentials retrieved successfully\n')

    log('\nTesting cluster interface configuration...\n')
    try:
        cphaconf = json.loads(
            subprocess.check_output(['cphaconf', 'aws_mode']))
    except Exception:
        raise Exception('''You do not seem to have a valid cluster
configuration
''')

    if not len([i for i in cphaconf[IFS] if i[TYPE] == INTERNAL]):
        raise Exception('''You do not seem to have internal interfaces defined
Please designate at least one interface as internal in the cluster topology tab
''')
    log('Cluster interface configuration tested successfully\n')
    endpoint = '.'.join(['ec2', region, domain])
    log('\nTesting connection to ' + endpoint + ':443...\n')
    cmd = ['nc', '-w', '5', '-z', endpoint, '443']
    try:
        subprocess.check_call(cmd)
    except Exception:
        traceback.print_exc()
        raise Exception('''Failed to connect to the AWS API endpoint
Please verify that outgoing connections over TCP port 443 (HTTPS) to the AWS
endpoint are allowed by the firewall security policy.
See:
http://docs.aws.amazon.com/general/latest/gr/rande.html#vpc_region
''')
    log('The connection was opened successfully\n')

    log('\nComparing the system clock to AWS\n')
    cmd = ['curl_cli', '--request', 'PUT',
           'http://169.254.169.254/latest/api/token',
           '--header', 'X-aws-ec2-metadata-token-ttl-seconds: 60']
    token = subprocess.check_output(cmd)
    if not token:
        raise Exception('Failed to get metadata token\n')
    cmd = ['curl_cli', '-Igs', '--header', 'X-aws-ec2-metadata-token: '
           + token.decode('utf-8'), META_DATA]
    lines = subprocess.check_output(cmd).decode('utf-8')
    for line in lines.split('\n'):
        if line.startswith('Date: '):
            d = line.partition('Date: ')[2]
            t1 = datetime.datetime(*eut.parsedate(d)[:6])
            t2 = datetime.datetime.utcnow()
            log('Time difference is ' + str(abs(t2 - t1)) + '\n')
            if abs(t2 - t1) > datetime.timedelta(seconds=5):
                raise Exception('''Your system clock is not set up properly
Please set up NTP.
''')
            break
    log('The system clock is synchronized\n')

    log('\nTesting AWS interface configuration...\n')
    update_cphaconf(cphaconf, region)
    for interface in cphaconf[IFS]:
        for attr in [AWSproperties.IPADDR.value, AWSproperties.OTHER_MEMBER_IF_IP.value]:
            try:
                aws_obj = aws.AWS(key_file='IAM')
                headers, body = aws_obj.request(
                    'ec2', region, 'GET', '/?' + urlencode({
                        'Action': 'DescribeNetworkInterfaces',
                        'Filter.0.Name': 'vpc-id',
                        'Filter.0.Value': vpc_id,
                        'Filter.1.Name': 'private-ip-address',
                        'Filter.1.Value': interface[attr],
                    }), '')
                if headers.get('_code') != '200':
                    raise Exception('Failed in AWS API request')
                interface['aws_' + attr] = aws.listify(
                    body, 'item')['networkInterfaceSet'][0]
            except Exception:
                traceback.print_exc()
                raise Exception('''Failed to retrieve interfaces from AWS
Please verify that the IAM role is set up correctly.
''')

    for interface in cphaconf[IFS]:
        for attr in [AWSproperties.IPADDR.value, AWSproperties.OTHER_MEMBER_IF_IP.value]:
            netifset = interface['aws_' + attr]
            if not netifset:
                raise Exception(
                    '''No ENI with primary address %s found
Please verify that %s is the primary and not secondary address
of the appropriate ENI (Elastic Network Interface)
''' % (interface[attr], interface[attr]))
            if netifset['sourceDestCheck'] == 'true':
                raise Exception(
                    'Please disable source/destination check on ' +
                    'interface with address %s\n' % interface[attr])

    if mode.load_deploy_mode() == mode.DEPLOY_MODE_CROSS_AZ:
        if not os.path.exists(CROSS_AZ_CLUSTER_SEC_IP_MAP):
            raise Exception(
                f"The File {CROSS_AZ_CLUSTER_SEC_IP_MAP} does not exist on this cluster member. Please delete the "
                f"file from another member (if exists) and run {AWS_HA_CLI_COMMAND} restart on both members")
        else:
            log('\nTesting Cross AZ Cluster IP pairs map is up to date...\n')
            for interface in cphaconf[IFS]:
                interface[AWSproperties.PEER_INTERFACE.value] = interface[f'aws_{AWSproperties.OTHER_MEMBER_IF_IP.value}']
                interface[AWSproperties.LOCAL_INTERFACE.value] = interface[f'aws_{AWSproperties.IPADDR.value}']
                update_cross_az_cluster_map(interface, CROSS_AZ_CLUSTER_SEC_IP_MAP_TEST, describe_flag=False)
            if not filecmp.cmp(CROSS_AZ_CLUSTER_SEC_IP_MAP_TEST, CROSS_AZ_CLUSTER_SEC_IP_MAP):
                raise Exception(f'The file {CROSS_AZ_CLUSTER_SEC_IP_MAP} is not updated. Please run '
                                f'{AWS_HA_CLI_COMMAND} restart on both members')
            log('\nTesting all private secondary IPs on active member have associated public IP...\n')
            for interface in cphaconf[IFS]:
                if interface[TYPE] == INTERNAL:
                    continue
                local_state, remote_state = mode.fetch_members_state()
                if not local_state or not remote_state:
                    raise Exception("Failed to extract local and remote members' states. Please verify 'cphaprob stat' "
                                    "command")
                if local_state == ACTIVE:
                    to_check = interface[AWSproperties.PEER_INTERFACE.value]
                elif local_state == STANDBY:
                    to_check = interface[AWSproperties.LOCAL_INTERFACE.value]
                else:
                    raise Exception("Unknown cluster member state. Check your cluster configuration.")
                private_ips_to_allocation_ids = get_all_allocation_ids(to_check)
                if len(private_ips_to_allocation_ids) != 0:
                    raise AssertionError(
                        "There are secondary public IPs that are associated to private IPs of the standby member. "
                        f"For moving all of them to the active member run {AWS_HA_CLI_COMMAND} restart on both members")

    log('\nAll tests were successful!\n')


def update_cphaconf(cphaconf: dict, region: str) -> None:
    """
    Update the cphaconf dictionary to contain only interfaces that appear in both the original cphaconf dictionary and in
    AWS portal after fetching them by using DescribeNetworkInterfaces request with the instance-id filter
    """
    instance_id = aws.metadata('/latest/meta-data/instance-id')
    try:
        aws_obj = aws.AWS(key_file='IAM')
        headers, body = aws_obj.request(
            'ec2', region, 'GET', '/?' + urlencode({
                'Action': 'DescribeNetworkInterfaces',
                'Filter.0.Name': 'attachment.instance-id',
                'Filter.0.Value': instance_id}), '')
        if headers.get('_code') != '200':
            raise Exception('Failed in AWS API request')
    except Exception:
        traceback.print_exc()
        raise Exception('''Failed to retrieve interfaces from AWS
    Please verify that the IAM role is set up correctly.
    ''')
    ec2_private_ips = []
    for item in body['networkInterfaceSet']['item']:
        ec2_private_ips.append(item['privateIpAddress'])
    cphaconf[IFS] = [interface for interface in cphaconf[IFS] if interface[AWSproperties.IPADDR.value] in ec2_private_ips]


def main():
    """#TODO fixDocString"""
    try:
        test()
    except Exception:
        log('Error:\n' + str(sys.exc_info()[1]) + '\n')
        sys.exit(1)


if __name__ == '__main__':
    main()


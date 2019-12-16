import os
import sys
from os.path import expanduser

def fail(m=None):
    sys.stdout.write("ASSERT: {}\n".format(m))
    sys.exit(1)

if not sys.version_info[0] in (2,3):
    fail("Unsupported Python Version: {}\n".format(sys.version_info[0]))

# module imports
try:
    import requests,urllib3,json,argparse,prettytable,signal,getpass
except:
    fail("Failed to import module\n{}".format(sys.exc_info()))

# disable ssl warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# functions
def read_kbd(user_prompt, allowed_values, default_value, flag_echo=True):
    if flag_echo == True:
        input_is_valid = False
        while not input_is_valid:
            if sys.version_info[0] == 3:
                user_input = input("{} [{}]: ".format(user_prompt,default_value))
            if sys.version_info[0] == 2:
                user_input = raw_input("{} [{}]: ".format(user_prompt,default_value))

            if user_input == "":
                user_input = default_value
                input_is_valid = True
            else:
                if len(allowed_values) == 0:
                    input_is_valid = True
                else:
                    if user_input in allowed_values:
                        input_is_valid = True
    else:
        user_input = getpass.getpass(prompt="{}: ".format(user_prompt), stream=None)

    return(user_input)


def login(du_host, username, password, project_name):
    url = "{}/keystone/v3/auth/tokens?nocatalog".format(du_host)
    body = {
        "auth": {
            "identity": {
                "methods": ["password"],
                "password": {
                    "user": {
                        "name": username,
                        "domain": {"id": "default"},
                        "password": password
                    }
                }
            },
            "scope": {
                "project": {
                    "name": project_name,
                    "domain": {"id": "default"}
                }
            }
        }
    }
    try:
        resp = requests.post(url, data=json.dumps(body), headers={'content-type': 'application/json'}, verify=False)
        json_response = json.loads(resp.text)
    except:
        fail_bootstrap("failed to parse json result")
    return json_response['token']['project']['id'], resp.headers['X-Subject-Token']


def login_du(du_url,du_user,du_password,du_tenant):
    try:
        project_id, token = login(du_url, du_user, du_password, du_tenant)
    except:
        return(None,None)

    return(project_id, token)


def get_host_metadata(du, project_id, token):
    host_metadata = {}
    region_type = get_du_type(du['url'], project_id, token)
    host_metadata['record_source'] = "User-Defined"
    host_metadata['ip'] = read_kbd("--> IP Address", [], '', True)
    host_metadata['hostname'] = read_kbd("--> Hostname", [], '', True)
    if region_type == "KVM":
        host_metadata['bond_config'] = read_kbd("--> Bond Config", [], '', True)
        host_metadata['nova'] = read_kbd("--> Enable Nova", ['y','n'], 'y', True)
        host_metadata['glance'] = read_kbd("--> Enable Glance", ['y','n'], 'n', True)
        host_metadata['cinder'] = read_kbd("--> Enable Cinder", ['y','n'], 'n', True)
        host_metadata['designate'] = read_kbd("--> Enable Designate", ['y','n'], 'n', True)
        host_metadata['node_type'] = ""
        host_metadata['pf9-kube'] = "n"
        host_metadata['cluster_name'] = ""
    elif region_type == "Kubernetes":
        host_metadata['bond_config'] = ""
        host_metadata['nova'] = ""
        host_metadata['glance'] = ""
        host_metadata['cinder'] = ""
        host_metadata['designate'] = ""
        host_metadata['pf9-kube'] = "y"
        host_metadata['node_type'] = read_kbd("--> Node Type [master, worker]", ['master','worker'], '', True)
        host_metadata['cluster_name'] = read_kbd("--> Cluster to Attach To", [], '', True)
    elif region_type == "VMware":
        sys.stdout.write("\nERROR: Unsupported region type: {}".format(region_type))

    return(host_metadata)

def get_du_creds():
    du_metadata = {}
    du_metadata['du_url'] = read_kbd("--> DU URL", [], '', True)
    du_metadata['du_user'] = read_kbd("--> DU Username", [], 'pf9-kubeheat', True)
    du_metadata['du_password'] = read_kbd("--> DU Password", [], '', False)
    du_metadata['du_tenant'] = read_kbd("--> DU Tenant", [], 'svc-pmo', True)
    du_metadata['region_name'] = read_kbd("--> Region Name", [], '', True)
    du_metadata['region_proxy'] = read_kbd("--> Proxy", [], '', True)
    du_metadata['region_dns'] = read_kbd("--> DNS Server (comma-delimited list or IPs)", [], '', True)

    du_metadata['region_auth_type'] = read_kbd("--> Authentication Type ['simple','ssh-key']", ['simple','ssh-key'], 'simple', True)
    du_metadata['auth_username'] = read_kbd("--> Username for Remote Access", [], '', True)
    if du_metadata['region_auth_type'] == "simple":
        du_metadata['auth_password'] = read_kbd("--> Password for Remote Access", [], '', False)
    else:
        du_metadata['auth_password'] = ""
  
    if du_metadata['region_auth_type'] == "ssh-key":
        du_metadata['auth_ssh_key'] = read_kbd("--> SSH Key for Remote Access", [], '', True)
    else:
        du_metadata['auth_ssh_key'] = ""

    du_metadata['region_bond_if_name'] = read_kbd("--> Interface Name (for OVS Bond)", [], 'bond0', True)
    du_metadata['region_bond_mode'] = read_kbd("--> Bond Mode", [], '1', True)
    du_metadata['region_bond_mtu'] = read_kbd("--> MTU for Bond Interface", [], '9000', True)
    return(du_metadata)


def qbert_is_responding(du_url, project_id, token):
    try:
        api_endpoint = "qbert/v3/{}/nodes".format(project_id)
        headers = { 'content-type': 'application/json', 'X-Auth-Token': token }
        pf9_response = requests.get("{}/{}".format(du_url,api_endpoint), verify=False, headers=headers)
        if pf9_response.status_code == 200:
            return True
    except:
        return False

    return False


def credsmanager_is_responding(du_url, project_id, token):
    try:
        api_endpoint = "credsmanager"
        headers = { 'content-type': 'application/json', 'X-Auth-Token': token }
        pf9_response = requests.get("{}/{}".format(du_url,api_endpoint), verify=False, headers=headers, timeout=5)
        if pf9_response.status_code == 200:
            return True
    except:
        return False

    return False


def discover_du_hosts(du_url, project_id, token):
    discovered_hosts = []
    try:
        api_endpoint = "resmgr/v1/hosts"
        headers = { 'content-type': 'application/json', 'X-Auth-Token': token }
        pf9_response = requests.get("{}/{}".format(du_url,api_endpoint), verify=False, headers=headers)
        if pf9_response.status_code != 200:
            return(discovered_hosts)
    except:
        return(discovered_hosts)

    # parse resmgr response
    try:
        json_response = json.loads(pf9_response.text)
    except:
        return(discovered_hosts)

    # process discovered hosts
    cnt = 0
    for host in json_response:
        #print("----------------------------------------------------------------------------")
        #sys.stdout.write("--> INTERFACES: {}\n".format(host['extensions']['interfaces']))
        #print("----------------------------------------------------------------------------")
        #sys.stdout.write("--> IP_ADDRESS: {}\n".format(host['extensions']['ip_address']))
        #print("============================================================================")
        #sys.exit(0)

        # get IP
        try:
            discover_ips = host['extensions']['ip_address']['data'][0]
        except:
            discover_ips = "no-data"

        # get roles
        role_kube = "n"
        role_nova = "n"
        role_glance = "n"
        role_cinder = "n"
        role_designate = "n"
        for role in host['roles']:
            if role == "pf9-kube":
                role_kube = "y"
            if role == "pf9-glance-role":
                role_glance = "y"
            if role == "pf9-cindervolume-base":
                role_cinder = "y"
            if role == "pf9-ostackhost-neutron":
                role_nova = "y"
            if role == "pf9-designate":
                role_designate = "y"

        host_record = {
            'du_url': du_url,
            'ip': discover_ips,
            'hostname': host['info']['hostname'],
            'record_source': "Discovered",
            'bond_config': "",
            'pf9-kube': role_kube,
            'nova': role_nova,
            'glance': role_glance,
            'cinder': role_cinder,
            'designate': role_designate,
            'node_type': "",
            'cluster_name': ""
        }
        discovered_hosts.append(host_record)

    return(discovered_hosts)


def get_du_hosts(du_url, project_id, token):
    num_hosts = 0
    try:
        api_endpoint = "resmgr/v1/hosts"
        headers = { 'content-type': 'application/json', 'X-Auth-Token': token }
        pf9_response = requests.get("{}/{}".format(du_url,api_endpoint), verify=False, headers=headers, timeout=5)
        if pf9_response.status_code != 200:
            return(num_hosts)

        try:
            json_response = json.loads(pf9_response.text)
        except:
            return(num_hosts)

        for item in json_response:
            num_hosts += 1
    except:
        return(num_hosts)

    return(num_hosts)


def get_du_type(du_url, project_id, token):
    region_type = "-"
    qbert_status = qbert_is_responding(du_url, project_id, token)
    if qbert_status == True:
        region_type = "Kubernetes"
    else:
        credsmanager_status = credsmanager_is_responding(du_url, project_id, token)
        if credsmanager_status == True:
            region_type = "KVM"
        else:
            region_type = "VMware"
    return(region_type)


def report_du_info(du_entries):
    from prettytable import PrettyTable

    if not os.path.isfile(CONFIG_FILE):
        sys.stdout.write("\nNo regions have been defined yet (run 'Add/Update Region')\n")
        return()

    du_table = PrettyTable()
    du_table.field_names = ["DU URL","Auth","Region Type","Region Name","Tenant","Proxy","# Hosts"]
    du_table.align["DU URL"] = "l"
    du_table.align["Auth"] = "l"
    du_table.align["Region Type"] = "l"
    du_table.align["Region Name"] = "l"
    du_table.align["Tenant"] = "l"
    du_table.align["Proxy"] = "l"
    du_table.align["# Hosts"] = "l"

    for du in du_entries:
        num_hosts = "-"
        project_id, token = login_du(du['url'],du['username'],du['password'],du['tenant'])
        if token == None:
            auth_status = "Failed"
            region_type = ""
        else:
            auth_status = "OK"
            region_type = get_du_type(du['url'], project_id, token)
            num_hosts = get_du_hosts(du['url'], project_id, token)

        du_table.add_row([du['url'], auth_status, region_type, du['region'], du['tenant'], du['proxy'], num_hosts])

    print(du_table)


def map_yn(map_key):
    if map_key == "y":
        return("Enabled")
    elif map_key == "n":
        return("Disabled")
    else:
        return("failed-to-map")

def report_host_info(host_entries):
    from prettytable import PrettyTable

    if not os.path.isfile(HOST_FILE):
        sys.stdout.write("\nNo hosts have been defined yet (run 'Add/Update Host')\n")
        return()

    if len(host_entries) == 0:
        sys.stdout.write("\nNo hosts have been defined yet (run 'Add/Update Host')\n")
        return()
    
    du_metadata = get_du_metadata(host_entries[0]['du_url'])
    project_id, token = login_du(du_metadata['url'],du_metadata['username'],du_metadata['password'],du_metadata['tenant'])
    if token == None:
        sys.stdout.write("--> failed to login to region")
    else:
        region_type = get_du_type(du_metadata['url'], project_id, token)

    if region_type == "KVM":
        host_table = PrettyTable()
        host_table.field_names = ["HOSTNAME","IP","Source","Nova","Glance","Cinder","Designate","Bond Config"]
        host_table.align["HOSTNAME"] = "l"
        host_table.align["IP"] = "l"
        host_table.align["Source"] = "l"
        host_table.align["Nova"] = "l"
        host_table.align["Glance"] = "l"
        host_table.align["Cinder"] = "l"
        host_table.align["Designate"] = "l"
        host_table.align["Bond Config"] = "l"
        for host in host_entries:
            host_table.add_row([host['hostname'],host['ip'], host['record_source'], map_yn(host['nova']), map_yn(host['glance']), map_yn(host['cinder']), map_yn(host['designate']), host['bond_config']])
        print(host_table)

    if region_type == "Kubernetes":
        host_table = PrettyTable()
        host_table.field_names = ["HOSTNAME","IP","Source","Node Type","Cluster Name"]
        host_table.align["HOSTNAME"] = "l"
        host_table.align["IP"] = "l"
        host_table.align["Source"] = "l"
        host_table.align["Node Type"] = "l"
        host_table.align["Cluster Name"] = "l"
        for host in host_entries:
            if host['cluster_name'] == "":
                cluster_assigned = "Unassigned"
            else:
                cluster_assigned = host['cluster_name']

            host_table.add_row([host['hostname'], host['ip'], host['record_source'], host['node_type'], cluster_assigned])
        print(host_table)


def select_du():
    if not os.path.isdir(CONFIG_DIR):
        sys.stdout.write("\nNo regions have been defined yet (run 'Add/Update Region')\n")
    elif not os.path.isfile(CONFIG_FILE):
        sys.stdout.write("\nNo regions have been defined yet (run 'Add/Update Region')\n")
    else:
        current_config = get_configs()
        if len(current_config) == 0:
            sys.stdout.write("\nNo regions have been defined yet (run 'Add/Update Region')\n")
        else:
            cnt = 1
            allowed_values = []
            sys.stdout.write("\n")
            for du in current_config:
                sys.stdout.write("{}. {}\n".format(cnt,du['url']))
                allowed_values.append(str(cnt))
                cnt += 1
            user_input = read_kbd("\nSelect Region", allowed_values, '', True)
            idx = int(user_input) - 1
            return(current_config[idx])
        return({})

def get_configs():
    du_configs = []
    if os.path.isfile(CONFIG_FILE):
        with open(CONFIG_FILE) as json_file:
            du_configs = json.load(json_file)

    return(du_configs)


def get_du_metadata(du_url):
    du_config = {}
    if os.path.isfile(CONFIG_FILE):
        with open(CONFIG_FILE) as json_file:
            du_configs = json.load(json_file)
        for du in du_configs:
            if du['url'] == du_url:
                du_config = dict(du)
                break

    return(du_config)


def get_hosts(du_url):
    du_hosts = []
    if os.path.isfile(HOST_FILE):
        with open(HOST_FILE) as json_file:
            du_hosts = json.load(json_file)

    if du_url == None:
        filtered_hosts = list(du_hosts)
    else:
        filtered_hosts = []
        for du in du_hosts:
            if du['du_url'] == du_url:
                filtered_hosts.append(du)

    return(filtered_hosts)


def write_host(host):
    if not os.path.isdir(CONFIG_DIR):
        try:
            os.mkdir(CONFIG_DIR)
        except:
            fail("failed to create directory: {}".format(CONFIG_DIR))

    current_hosts = get_hosts(None)
    if len(current_hosts) == 0:
        current_hosts.append(host)
        with open(HOST_FILE, 'w') as outfile:
            json.dump(current_hosts, outfile)
    else:
        update_hosts = []
        flag_found = False
        for h in current_hosts:
            if h['hostname'] == host['hostname']:
                update_hosts.append(host)
                flag_found = True
            else:
                update_hosts.append(h)
        if not flag_found:
            update_hosts.append(host)
        with open(HOST_FILE, 'w') as outfile:
            json.dump(update_hosts, outfile)


def write_config(du):
    if not os.path.isdir(CONFIG_DIR):
        try:
            os.mkdir(CONFIG_DIR)
        except:
            fail("failed to create directory: {}".format(CONFIG_DIR))

    current_config = get_configs()
    if len(current_config) == 0:
        current_config.append(du)
        with open(CONFIG_FILE, 'w') as outfile:
            json.dump(current_config, outfile)
    else:
        update_config = []
        flag_found = False
        for config in current_config:
            if config['url'] == du['url']:
                update_config.append(du)
                flag_found = True
            else:
                update_config.append(config)
        if not flag_found:
            update_config.append(du)
        with open(CONFIG_FILE, 'w') as outfile:
            json.dump(update_config, outfile)


def add_host(du):
    sys.stdout.write("\nAdding Host to Region: {}\n".format(du['url']))
    project_id, token = login_du(du['url'],du['username'],du['password'],du['tenant'])
    if token == None:
        sys.stdout.write("--> failed to login to region")
    else:
        host_metadata = get_host_metadata(du, project_id, token)
        host = {
            'du_url': du['url'],
            'ip': host_metadata['ip'],
            'hostname': host_metadata['hostname'],
            'record_source': host_metadata['record_source'],
            'bond_config': host_metadata['bond_config'],
            'pf9-kube': host_metadata['pf9-kube'],
            'nova': host_metadata['nova'],
            'glance': host_metadata['glance'],
            'cinder': host_metadata['cinder'],
            'designate': host_metadata['designate'],
            'node_type': host_metadata['node_type'],
            'cluster_name': host_metadata['cluster_name']
        }

        # persist configurtion
        write_host(host)


def add_region():
    sys.stdout.write("\nAdding Region:\n")
    du_metadata = get_du_creds()
    du = {
        'url': du_metadata['du_url'],
        'username': du_metadata['du_user'],
        'password': du_metadata['du_password'],
        'tenant': du_metadata['du_tenant'],
        'region': du_metadata['region_name'],
        'proxy': du_metadata['region_proxy'],
        'dns_list': du_metadata['region_dns'],
        'auth_type': du_metadata['region_auth_type'],
        'auth_ssh_key': du_metadata['auth_ssh_key'],
        'auth_password': du_metadata['auth_password'],
        'auth_username': du_metadata['auth_username'],
        'bond_ifname': du_metadata['region_bond_if_name'],
        'bond_mode': du_metadata['region_bond_mode'],
        'bond_mtu': du_metadata['region_bond_mtu']
    }

    # discovery existing hosts
    project_id, token = login_du(du['url'],du['username'],du['password'],du['tenant'])
    discoverd_hosts = discover_du_hosts(du['url'], project_id, token)
    for host in discoverd_hosts:
        write_host(host)

    # persist configurtion
    write_config(du)

    # return
    return(du)


def display_menu():
    sys.stdout.write("*****************************************\n")
    sys.stdout.write("**              Main Menu              **\n")
    sys.stdout.write("*****************************************\n")
    sys.stdout.write("1. Add/Edit Region\n")
    sys.stdout.write("2. Add/Edit Hosts\n")
    sys.stdout.write("3. Show Region\n")
    sys.stdout.write("4. Show Hosts\n")
    sys.stdout.write("5. Attach Hosts to Region\n")
    sys.stdout.write("*****************************************\n")


def cmd_loop():
    user_input = ""
    while not user_input in ['q','Q']:
        display_menu()
        user_input = read_kbd("Enter Selection ('q' to quit)", [], '', True)
        if user_input == '1':
            new_du = add_region()
            new_du_list = []
            new_du_list.append(new_du)
            report_du_info(new_du_list)
        elif user_input == '2':
            selected_du = select_du()
            new_host = add_host(selected_du)
        elif user_input == '3':
            du_entries = get_configs()
            report_du_info(du_entries)
        elif user_input == '4':
            selected_du = select_du()
            if selected_du != None:
                host_entries = get_hosts(selected_du['url'])
                report_host_info(host_entries)
        elif user_input == '5':
            None
        elif user_input in ['q','Q']:
            None
        else:
            sys.stdout.write("ERROR: Invalid Selection\n")
        sys.stdout.write("\n")


## main

# globals
HOME_DIR = expanduser("~")
CONFIG_DIR = "{}/.pf9-wizard".format(HOME_DIR)
CONFIG_FILE = "{}/du.conf".format(CONFIG_DIR)
HOST_FILE = "{}/hosts.conf".format(CONFIG_DIR)

# main menu loop
cmd_loop()

# vim: ai ts=4 sts=4 et sw=4 ft=python fdm=indent et foldlevel=0
import os

from fabric.api import sudo, task, env, execute
from fabric.context_managers import cd, settings, hide
from bookshelf.api_v1 import (apt_install,
                              create_docker_group,
                              create_server,
                              destroy as f_destroy,
                              down as f_down,
                              ec2,
                              git_clone,
                              install_docker,
                              is_there_state,
                              load_state_from_disk,
                              log_green,
                              up as f_up)

from cuisine import (user_ensure,
                     group_ensure,
                     group_user_ensure)

# Modify some global Fabric behaviours:
# Let's disable known_hosts, since on Clouds that behaviour can get in the
# way as we continuosly destroy/create boxes.
env.disable_known_hosts = True
env.use_ssh_config = False
env.eagerly_disconnect = True
env.connection_attemtps = 5
env.user = 'root'


class BenchmarkServerCookbook():
    """
    Collection of helper functions for fabric tasks that are
    used for managing the Benchmarking service.
    """

    def add_user_to_docker_group(self):
        """
        Make sure the ubuntu user is part of the docker group.
        """
        log_green('adding the ubuntu user to the docker group')
        with settings(hide('warnings', 'running', 'stdout', 'stderr'),
                      warn_only=True, capture=True):
            user_ensure('ubuntu', home='/home/ubuntu', shell='/bin/bash')
            group_ensure('docker', gid=55)
            group_user_ensure('docker', 'ubuntu')

    def install_docker(self):
        create_docker_group()
        self.add_user_to_docker_group()
        install_docker()

    def install_docker_compose(self):
        cmd = "curl -L https://github.com/docker/compose/releases/download/1.5.2/docker-compose-`uname -s`-`uname -m` > /usr/local/bin/docker-compose"  # noqa
        sudo(cmd)
        sudo("chmod +x /usr/local/bin/docker-compose")

    def install_packages(self):
        """
        Install required packages.
        """
        apt_install(packages=self.required_packages())
        self.install_docker()
        self.install_docker_compose()

    def required_packages(self):
        """
        :return list: The required packages for this instance.
        """
        return ["git"]

    def start_benchmark_service(self):
        """
        Start the benchmark results service.
        """
        repo = 'benchmark-server'
        git_clone('https://github.com/ClusterHQ/benchmark-server',
                  repo)

        with cd(repo):
            sudo("docker-compose up")


cloud_config = {
    'ami': 'ami-87bea5b7',
    'distribution': 'ubuntu14.04',
    'username': 'ubuntu',
    'disk_name': '/dev/sda1',
    'disk_size': '40',
    'instance_type': os.getenv('AWS_INSTANCE_TYPE', 't2.micro'),
    'key_pair': os.environ['AWS_KEY_PAIR'],
    'region': os.getenv('AWS_REGION', 'us-west-2'),
    'secret_access_key': os.environ['AWS_SECRET_ACCESS_KEY'],
    'access_key_id': os.environ['AWS_ACCESS_KEY_ID'],
    'security_groups': ['ssh', 'benchmark-service'],
    'instance_name': 'benchmark_service',
    'description': 'Store results from benchmarking runs',
    'key_filename': os.environ['AWS_KEY_FILENAME'],
    'tags': {'name': 'benchmark_service'}
}


@task(default=True)
def help():
    """
    Print the help text.
    """

    help_text = (
        """
        Start an AWS instance that is running the Benchmarking results
        service.

        usage: fab <action>

        # Start the service
        $ fab it

        # Provision and start the AWS instance if it does not exist,
        # otherwise start an existing instance.
        $ fab up

        # Suspend the instance.
        $ fab down

        # Destroy the instance.
        $ fab destroy
        """
    )
    print help_text


@task
def destroy():
    """
    Destroy an existing instance.
    """
    if is_there_state():
        data = load_state_from_disk()
        cloud_type = data['cloud_type']
        region = data['region']
        access_key_id = cloud_config['access_key_id']
        secret_access_key = cloud_config['secret_access_key']
        instance_id = data['id']
        env.user = data['username']
        env.key_filename = cloud_config['key_filename']

        f_destroy(cloud=cloud_type,
                  region=region,
                  instance_id=instance_id,
                  access_key_id=access_key_id,
                  secret_access_key=secret_access_key)


@task
def down():
    """
    Halt an existing instance.
    """

    if is_there_state():
        data = load_state_from_disk()
        region = data['region']
        cloud_type = data['cloud_type']
        access_key_id = cloud_config['access_key_id']
        secret_access_key = cloud_config['secret_access_key']
        instance_id = data['id']
        env.key_filename = cloud_config['key_filename']

        ec2()
        f_down(cloud=cloud_type,
               instance_id=instance_id,
               region=region,
               access_key_id=access_key_id,
               secret_access_key=secret_access_key)


@task
def up():
    """
    Boots a new Ubuntu instance on AWS, or start the existing instance.
    """

    if is_there_state():
        data = load_state_from_disk()
        cloud_type = data['cloud_type']
        username = data['username']
        region = data['region']
        access_key_id = cloud_config['access_key_id']
        secret_access_key = cloud_config['secret_access_key']
        instance_id = data['id']
        env.user = data['username']
        env.key_filename = cloud_config['key_filename']

        ec2()

        f_up(cloud=cloud_type,
             region=region,
             instance_id=instance_id,
             access_key_id=access_key_id,
             secret_access_key=secret_access_key,
             username=username)
    else:
        env.user = cloud_config['username']
        env.key_filename = cloud_config['key_filename']

        # No state file exists so create a new VM and use the default
        # values from the 'cloud_config' dictionary
        create_server(cloud='ec2',
                      region=cloud_config['region'],
                      access_key_id=cloud_config['access_key_id'],
                      secret_access_key=cloud_config['secret_access_key'],
                      distribution=cloud_config['distribution'],
                      disk_name=cloud_config['disk_name'],
                      disk_size=cloud_config['disk_size'],
                      ami=cloud_config['ami'],
                      key_pair=cloud_config['key_pair'],
                      instance_type=cloud_config['instance_type'],
                      instance_name=cloud_config['instance_name'],
                      username=cloud_config['username'],
                      security_groups=cloud_config['security_groups'],
                      tags=cloud_config['tags'])
        if is_there_state():
            data = load_state_from_disk()
            env.hosts = data['ip_address']
            env.cloud = data['cloud_type']


@task
def it():
    execute(up)
    execute(bootstrap)


@task
def bootstrap():
    cookbook = BenchmarkServerCookbook()

    cookbook.install_packages()
    cookbook.start_benchmark_service()

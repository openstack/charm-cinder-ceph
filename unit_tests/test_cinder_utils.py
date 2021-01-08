# Copyright 2016 Canonical Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from unittest.mock import patch, call
import os
import cinder_utils as cinder_utils

from test_utils import (
    CharmTestCase,
)

TO_PATCH = [
    # helpers.core.hookenv
    'relation_ids',
    'service_name',
    'application_name',
    # storage_utils
    'get_os_codename_package',
    'templating',
    'install_alternative',
    'mkdir'
]


MOUNTS = [
    ['/mnt', '/dev/vdb']
]


class TestCinderUtils(CharmTestCase):

    def setUp(self):
        super(TestCinderUtils, self).setUp(cinder_utils, TO_PATCH)
        self.service_name.return_value = 'cinder-ceph'

    @patch('os.path.exists')
    def test_register_configs_ceph(self, exists):
        exists.return_value = True
        self.get_os_codename_package.return_value = 'grizzly'
        self.relation_ids.return_value = ['ceph:0']
        configs = cinder_utils.register_configs()
        calls = []
        for conf in [cinder_utils.ceph_config_file()]:
            calls.append(
                call(conf,
                     cinder_utils.CONFIG_FILES[conf]['hook_contexts'])
            )
        configs.register.assert_has_calls(calls, any_order=True)
        self.mkdir.assert_has_calls([
            call(os.path.dirname(cinder_utils.ceph_config_file())),
            call(os.path.dirname(cinder_utils.CEPH_CONF))
        ])
        self.install_alternative.assert_called_with(
            os.path.basename(cinder_utils.CEPH_CONF),
            cinder_utils.CEPH_CONF, cinder_utils.ceph_config_file()
        )

    def test_set_ceph_kludge(self):
        pass
        """
        def set_ceph_env_variables(service):
            # XXX: Horrid kludge to make cinder-volume use
            # a different ceph username than admin
            env = open('/etc/environment', 'r').read()
            if 'CEPH_ARGS' not in env:
                with open('/etc/environment', 'a') as out:
                    out.write('CEPH_ARGS="--id %s"\n' % service)
            with open('/etc/init/cinder-volume.override', 'w') as out:
                    out.write('env CEPH_ARGS="--id %s"\n' % service)
        """

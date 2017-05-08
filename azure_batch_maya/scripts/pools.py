﻿#-------------------------------------------------------------------------
#
# Azure Batch Maya Plugin
#
# Copyright (c) Microsoft Corporation.  All rights reserved.
#
# MIT License
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the ""Software""), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED *AS IS*, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
#
#--------------------------------------------------------------------------

import os
import logging
import uuid
import datetime

from azure.batch import models

import utils
from api import MayaAPI as maya
from ui_pools import PoolsUI



class AzureBatchPools(object):
    """Handler for pool functionality."""
    
    def __init__(self, frame, call):
        """Create new Pool Handler.

        :param frame: The shared plug-in UI frame.
        :type frame: :class:`.AzureBatchUI`
        :param func call: The shared REST API call wrapper.
        """
        self._log = logging.getLogger('AzureBatchMaya')
        self._call = call               
        self._session = None

        self.batch = None
        self.ui = PoolsUI(self, frame)
        self.pools = []
        self.selected_pool = None

    def configure(self, session, env):
        """Populate the Batch client for the current sessions of the pools tab.
        Called on successful authentication.
        :param session: Authenticated configuration handler.
        :type session: :class:`.AzureBatchConfig`
        :param env: Render node environment handler.
        :type env: :class:`.AzureBatchEnvironment`
        """
        self._session = session
        self.batch = self._session.batch
        self.environment = env

    def list_pools(self, lazy=False):
        """Retrieves the currently running pools. Is called on loading and
        refreshing the pools tab, also when populating the job submission
        pool selection drop down menu.
        """
        #if lazy and self.pools:
        #    return [pool.id for pool in self.pools if not pool.auto]
        self.pools = [p for p in self._call(self.batch.pool.list) if p.id.startswith('Maya_')]
        self.pools.sort(key=lambda x: x.creation_time, reverse=True)
        self.count = len(self.pools)
        return [pool.id for pool in self.pools if pool.id.startswith("Maya_Pool")]

    def get_pools(self):
        """Retrieves the currently running pools and populates the UI
        list. Is called on loading and refreshing the pools tab.
        """
        self.list_pools()
        display_pools = []
        for index, pool in enumerate(self.pools):
            name = pool.display_name if pool.display_name else pool.id
            display_pools.append(self.ui.create_pool_entry(name, index))
        return display_pools

    def pool_selected(self, pool_ui):
        """Function called when opening and closing the pool details
        expanding sections on the UI.
        """
        if self.selected_pool and pool_ui:
            self.selected_pool.collapse()
        self.selected_pool = pool_ui
        if pool_ui:
            self.update_pool(pool_ui.index)

    def update_pool(self, index):
        """Update the display for the currently selected pool. This is called
        when a specific pool is selected or refreshed in the UI.
        """
        try:
            pool = self.pools[index]
            self.selected_pool.set_label("loading...")
            maya.refresh()
            pool = self._call(self.batch.pool.get, pool.id)
            _nodes = self._call(self.batch.compute_node.list, pool.id)
            nodes = [n for n in _nodes]
            self.selected_pool.set_label(pool.display_name if pool.display_name else pool.id)
            self.selected_pool.set_size(pool.current_dedicated_nodes)
            self.selected_pool.set_target(pool.target_dedicated_nodes)
            self.selected_pool.set_type(
                "Auto" if pool.id.startswith("Maya_Auto_Pool") else "Provisioned")
            self.selected_pool.set_state(pool.state.value, nodes)
            self.selected_pool.set_tasks(pool.max_tasks_per_node)
            self.selected_pool.set_allocation(pool.allocation_state.value)
            self.selected_pool.set_created(pool.creation_time)
            self.selected_pool.set_licenses(pool.application_licenses)
            self.selected_pool.set_vm_sku(pool.vm_size)
            maya.refresh()
        except Exception as exp:
            self._log.warning(str(exp))
            self.ui.refresh()

    def is_auto_pool(self):
        """Returns whether the selected pool is an auto-pool or a 
        persistant pool.
        """
        try:
            pool = self.pools[self.selected_pool.index]
            return pool.id.startswith("Maya_Auto_Pool")
        except (IndexError, TypeError, AttributeError) as exp:
            self._log.info("Unable to retrieve selected pool {0}".format(exp))
            return False

    def delete_pool(self):
        """Delete the currently selected pool."""
        try:
            pool = self.pools[self.selected_pool.index]
            self._log.info("Deleting pool '{}'.".format(pool.id))
            self._call(self.batch.pool.delete, pool.id)
        except (IndexError, TypeError, AttributeError) as exp:
            self._log.info("Unable to retrieve selected pool {0}".format(exp))
            return
        finally:
            self.ui.refresh()
    
    def get_pool_size(self):
        """Get the target number of VMs in the selected pool."""
        try:
            pool = self.pools[self.selected_pool.index]
            return int(pool.target_dedicated_nodes)
        except (IndexError, TypeError, AttributeError) as exp:
            self._log.info("Failed to parse pool target size {0}".format(exp))
            return 0

    def get_pool_os(self, pool_id):
        """Get the OS flavor of the specified pool ID."""
        try:
            pool = self._call(self.batch.pool.get, pool_id)
            return self.environment.os_flavor(pool.virtual_machine_configuration.image_reference)
        except AttributeError:
            raise ValueError('Selected pool is not a valid Maya pool.')

    def create_pool(self, size, name):
        """Create and deploy a new pool.
        Called on job submission by submission.py.
        TODO: Support auto-scale formula.
        """
        image = self.environment.get_image()
        node_agent_sku_id = image.pop('node_sku_id')
        pool_id = 'Maya_Pool_{}'.format(uuid.uuid4())
        pool_config = models.VirtualMachineConfiguration(
            image_reference=models.ImageReference(**image),
            node_agent_sku_id=node_agent_sku_id)
        self._log.info("Creating new pool '{}' with {} VMs.".format(name, size))
        new_pool = models.PoolAddParameter(
            id=pool_id,
            display_name="Maya Pool for {}".format(name),
            resize_timeout=datetime.timedelta(minutes=30),
            application_licenses=self.environment.get_application_licenses(),
            vm_size=self.environment.get_vm_sku(),
            virtual_machine_configuration=pool_config,
            target_dedicated_nodes=int(size),
            max_tasks_per_node=1)
        self._call(self.batch.pool.add, new_pool)
        self._log.debug("Successfully created pool.")
        return {"poolId" : pool_id}

    def create_auto_pool(self, size, job_name):
        """Create a JSON auto pool specification.
        Called on job submission by submission.py.
        """
        image = self.environment.get_image()
        node_agent_sku_id = image.pop('node_sku_id')
        pool_config = {
            'imageReference': image,
            'nodeAgentSKUId': node_agent_sku_id}
        pool_spec = {
            'vmSize': self.environment.get_vm_sku(),
            'displayName': "Auto Pool for {}".format(job_name),
            'virtualMachineConfiguration': pool_config,
            'maxTasksPerNode': 1,
            'applicationLicenses': self.environment.get_application_licenses(),
            'targetDedicatedNodes': int(size)}
        auto_pool = {
            'autoPoolIdPrefix': "Maya_Auto_Pool_",
            'poolLifetimeOption': "job",
            'keepAlive': False,
            'pool': pool_spec}      
        return {'autoPoolSpecification': auto_pool}

    def resize_pool(self, new_size):
        """Resize an existing pool."""
        try:
            pool = self.pools[self.selected_pool.index]
            self._log.info("Resizing pool '{}' to {} VMs".format(pool.id, new_size))
            self._call(self.batch.pool.resize, pool.id, {'target_dedicated_nodes':int(new_size),
                                                         'target_low_priority_nodes': 0})
            maya.refresh()
        except Exception as exp:
            self._log.info("Failed to resize pool {0}".format(exp))
            self.ui.refresh()


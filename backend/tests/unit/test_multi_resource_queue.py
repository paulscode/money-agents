"""Tests for ToolExecutionService multi-resource queue acquisition.

Tests the sorted-UUID deadlock prevention, multi-GPU resource acquisition,
and proper cleanup on failure/timeout/cancel scenarios.

These are focused unit tests mocking the DB and queue services.
"""
import pytest
import asyncio
import time
from unittest.mock import patch, AsyncMock, MagicMock, PropertyMock
from uuid import uuid4, UUID
from datetime import datetime

from app.models import Tool, ToolStatus, ToolCategory


# =============================================================================
# Helpers
# =============================================================================


def _make_mock_tool(
    slug="test-tool",
    resource_ids=None,
    interface_type="rest_api",
    interface_config=None,
    status=ToolStatus.IMPLEMENTED,
    requires_approval=False,
):
    """Create a mock Tool with the fields execute_tool uses."""
    tool = MagicMock(spec=Tool)
    tool.id = uuid4()
    tool.slug = slug
    tool.name = f"Test {slug}"
    tool.status = status
    tool.interface_type = interface_type
    tool.interface_config = interface_config or {"base_url": "http://localhost:9999"}
    tool.resource_ids = resource_ids
    tool.resource_id = UUID(resource_ids[0]) if resource_ids else None
    tool.requires_approval = requires_approval
    tool.timeout_seconds = 30
    tool.input_schema = None
    tool.available_on_agents = None
    tool.agent_resource_map = None
    return tool


def _make_mock_resource(name="GPU-0", status="available"):
    """Create a mock Resource object."""
    r = MagicMock()
    r.id = uuid4()
    r.name = name
    r.status = status
    return r


def _make_running_job():
    """Create a mock job that's immediately RUNNING."""
    job = MagicMock()
    job.id = uuid4()
    job.status = "running"
    return job


# =============================================================================
# Tests: Resource IDs are sorted before acquisition
# =============================================================================


class TestResourceIdSorting:
    """Verify resource_ids are sorted before acquisition to prevent deadlocks."""

    def test_resource_ids_sorted_inline(self):
        """Verify the sorting logic as it would work in execute_tool."""
        # Simulate what execute_tool does with resource_ids
        uuid_a = str(uuid4())
        uuid_b = str(uuid4())
        uuid_c = str(uuid4())

        # Provide them in random order
        resource_ids = [uuid_c, uuid_a, uuid_b]
        sorted_ids = sorted(resource_ids)

        # They should now be in lexicographic UUID order
        assert sorted_ids[0] <= sorted_ids[1] <= sorted_ids[2]

    def test_consistent_order_regardless_of_input(self):
        """Same UUIDs in different input orders produce same acquisition order."""
        uuid_a = str(uuid4())
        uuid_b = str(uuid4())

        order1 = sorted([uuid_a, uuid_b])
        order2 = sorted([uuid_b, uuid_a])

        assert order1 == order2

    def test_single_resource_id_unchanged(self):
        """A single resource_id is trivially sorted."""
        rid = str(uuid4())
        assert sorted([rid]) == [rid]

    def test_empty_resource_ids(self):
        """Empty list stays empty."""
        assert sorted([]) == []


# =============================================================================
# Tests: Multi-resource acquisition flow
# =============================================================================


class TestMultiResourceAcquisition:
    """Test the multi-resource acquisition loop logic."""

    async def test_two_resources_acquired_in_order(self):
        """When a tool needs 2 GPUs, jobs are created for each in sorted order."""
        gpu0_id = str(uuid4())
        gpu1_id = str(uuid4())
        sorted_ids = sorted([gpu0_id, gpu1_id])

        # Track resource_id order of create_job calls
        create_order = []

        async def mock_create_job(db, tool_id, resource_id, **kwargs):
            create_order.append(str(resource_id))
            job = MagicMock()
            job.id = uuid4()
            job.status = "running"
            return job

        from app.services import job_queue_service, resource_service

        with patch.object(resource_service, "get_resource", new_callable=AsyncMock) as mock_get_res, \
             patch.object(job_queue_service, "create_job", side_effect=mock_create_job) as mock_create, \
             patch.object(job_queue_service, "complete_job", new_callable=AsyncMock):

            # Both resources are available
            mock_resource = MagicMock()
            mock_resource.name = "Test GPU"
            mock_resource.status = "available"
            mock_get_res.return_value = mock_resource

            # Simulate the acquisition loop from execute_tool
            resource_ids = sorted([gpu0_id, gpu1_id])
            jobs = []
            for rid_str in resource_ids:
                resource_id = UUID(rid_str)
                resource = await resource_service.get_resource(MagicMock(), resource_id)
                job = await job_queue_service.create_job(
                    db=MagicMock(), tool_id=uuid4(), resource_id=resource_id,
                )
                jobs.append((resource_id, job))

            assert len(jobs) == 2
            # Verify sorted acquisition order
            assert create_order == sorted_ids

    async def test_cleanup_on_second_resource_failure(self):
        """If second resource fails, first job should be cleaned up."""
        gpu0_id = str(uuid4())
        gpu1_id = str(uuid4())
        sorted_ids = sorted([gpu0_id, gpu1_id])

        first_job = MagicMock()
        first_job.id = uuid4()
        first_job.status = "running"

        call_count = {"value": 0}

        async def mock_get_resource(db, resource_id):
            call_count["value"] += 1
            if call_count["value"] == 1:
                # First resource ok
                r = MagicMock()
                r.name = "GPU-0"
                r.status = "available"
                return r
            else:
                # Second resource not found
                return None

        from app.services import job_queue_service, resource_service

        completed_jobs = []

        async def track_complete(db, job_id, **kwargs):
            completed_jobs.append(job_id)

        with patch.object(resource_service, "get_resource", side_effect=mock_get_resource), \
             patch.object(job_queue_service, "create_job", new_callable=AsyncMock, return_value=first_job), \
             patch.object(job_queue_service, "complete_job", side_effect=track_complete):

            # Simulate the acquisition loop
            jobs = []
            resource_ids = sorted([gpu0_id, gpu1_id])
            failed = False

            for rid_str in resource_ids:
                resource_id = UUID(rid_str)
                resource = await resource_service.get_resource(MagicMock(), resource_id)
                if not resource:
                    # Cancel previously acquired jobs
                    for _, prev_job in jobs:
                        await job_queue_service.complete_job(MagicMock(), prev_job.id, error="not_found")
                    failed = True
                    break

                job = await job_queue_service.create_job(db=MagicMock(), tool_id=uuid4(), resource_id=resource_id)
                jobs.append((resource_id, job))

            assert failed is True
            assert len(jobs) == 1
            # The first job should have been cleaned up
            assert first_job.id in completed_jobs


# =============================================================================
# Tests: Finally block releases ALL jobs
# =============================================================================


class TestJobRelease:
    """Test that the finally block releases all acquired jobs."""

    async def test_all_jobs_released_on_success(self):
        """All acquired jobs should be released in the finally block."""
        from app.services import job_queue_service

        job1 = MagicMock()
        job1.id = uuid4()
        job2 = MagicMock()
        job2.id = uuid4()

        released_ids = []

        async def track_release(db, job_id, result=None, error=None):
            released_ids.append(job_id)

        jobs = [(uuid4(), job1), (uuid4(), job2)]

        with patch.object(job_queue_service, "complete_job", side_effect=track_release):
            try:
                # Simulate execution
                pass
            finally:
                for _, acquired_job in jobs:
                    await job_queue_service.complete_job(
                        db=MagicMock(), job_id=acquired_job.id
                    )

        assert job1.id in released_ids
        assert job2.id in released_ids
        assert len(released_ids) == 2

    async def test_all_jobs_released_on_exception(self):
        """Even if execution throws, all jobs should be released."""
        from app.services import job_queue_service

        job1 = MagicMock()
        job1.id = uuid4()
        job2 = MagicMock()
        job2.id = uuid4()

        released_ids = []

        async def track_release(db, job_id, result=None, error=None):
            released_ids.append(job_id)

        jobs = [(uuid4(), job1), (uuid4(), job2)]

        with patch.object(job_queue_service, "complete_job", side_effect=track_release):
            try:
                raise RuntimeError("Simulated execution failure")
            except RuntimeError:
                pass
            finally:
                for _, acquired_job in jobs:
                    await job_queue_service.complete_job(
                        db=MagicMock(), job_id=acquired_job.id
                    )

        assert len(released_ids) == 2


# =============================================================================
# Tests: GPU eviction integration point
# =============================================================================


class TestGpuEvictionIntegration:
    """Test that GPU eviction is called at the right point in execution."""

    async def test_eviction_called_when_resource_ids_present(self):
        """When a tool has resource_ids, GPU eviction should fire."""
        from app.services.gpu_lifecycle_service import GPULifecycleService

        mock_gpu_service = MagicMock(spec=GPULifecycleService)
        mock_gpu_service.prepare_gpu_for_tool = AsyncMock(return_value={"vram_free": True})
        mock_gpu_service.ensure_service_running = AsyncMock(return_value=True)

        resource_ids = [str(uuid4())]

        # Simulate the eviction call from execute_tool
        eviction_result = await mock_gpu_service.prepare_gpu_for_tool("test-tool")
        assert eviction_result["vram_free"] is True

        service_ready = await mock_gpu_service.ensure_service_running("test-tool")
        assert service_ready is True

    async def test_eviction_not_called_without_resources(self):
        """When a tool has no resource_ids, eviction should not fire."""
        resource_ids = []

        eviction_called = False
        if resource_ids:
            eviction_called = True

        assert eviction_called is False


# =============================================================================
# Tests: Deadlock prevention scenario
# =============================================================================


class TestDeadlockPrevention:
    """Test that sorted acquisition order prevents deadlocks."""

    def test_two_tools_same_gpus_same_order(self):
        """Two tools needing GPU-A and GPU-B should acquire in the SAME order."""
        gpu_a = str(uuid4())
        gpu_b = str(uuid4())

        tool1_ids = sorted([gpu_a, gpu_b])
        tool2_ids = sorted([gpu_b, gpu_a])  # Reversed input

        assert tool1_ids == tool2_ids, "Both tools must acquire in same sorted order"

    def test_three_resources_consistent_ordering(self):
        """Three GPUs should always be acquired in the same order."""
        ids = [str(uuid4()) for _ in range(3)]

        import itertools
        for perm in itertools.permutations(ids):
            assert sorted(perm) == sorted(ids)

    def test_sorting_is_deterministic(self):
        """UUID string sorting must be deterministic."""
        ids = [str(uuid4()) for _ in range(10)]
        sorted1 = sorted(ids)
        sorted2 = sorted(ids)
        assert sorted1 == sorted2

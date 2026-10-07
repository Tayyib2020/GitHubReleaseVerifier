"""Host compatibility for the installed GenLayer direct test runner.

The installed runner's Windows path unlinks a temporary file while file
descriptor zero still owns it. Windows correctly rejects that unlink. This
test-only replacement supplies the same encoded message through a pipe.
"""

import os


def pytest_configure():
    from gltest.direct import loader

    def inject_message_through_pipe(vm):
        from genlayer.py import calldata
        from genlayer.py.types import Address

        sender = Address(vm.sender) if isinstance(vm.sender, bytes) else vm.sender
        contract = (
            Address(vm._contract_address)
            if isinstance(vm._contract_address, bytes)
            else vm._contract_address
        )
        origin = Address(vm.origin) if isinstance(vm.origin, bytes) else vm.origin
        message = {
            "contract_address": contract,
            "sender_address": sender,
            "origin_address": origin,
            "stack": [],
            "value": vm._value,
            "datetime": vm._datetime,
            "is_init": False,
            "chain_id": vm._chain_id,
            "entry_kind": 0,
            "entry_data": b"",
            "entry_stage_data": None,
        }
        payload = calldata.encode(message)
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, payload)
        finally:
            os.close(write_fd)
        vm._original_stdin_fd = os.dup(0)
        os.dup2(read_fd, 0)
        os.close(read_fd)

    loader._inject_message_to_fd0 = inject_message_through_pipe

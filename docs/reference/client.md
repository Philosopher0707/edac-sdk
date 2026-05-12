# Client Reference

## Async Client

::: edac.client.client.EdacClient
    handler: python
    options:
      show_source: false
      show_root_heading: true
      show_signature: true
      show_docstring_attributes: true
      members:
        - wait_for_task
        - stream_events
        - watch_task
        - list_agents
        - list_tasks
        - submit_task
        - submit_tasks_batch
        - get_health
        - close

## Sync Client

::: edac.client.sync_client.EdacClientSync
    handler: python
    options:
      show_source: false
      show_root_heading: true
      show_signature: true
      members:
        - wait_for_task
        - stream_events
        - watch_task
        - list_agents
        - list_tasks
        - submit_task
        - submit_tasks_batch
        - get_health
        - close

## Exceptions

::: edac.client.exceptions.EdacClientError
    handler: python
    options:
      show_source: false
      show_root_heading: true
      show_docstring: true

::: edac.client.exceptions.EdacAPIError
    handler: python
    options:
      show_source: false
      show_root_heading: true

::: edac.client.exceptions.EdacAuthError
    handler: python
    options:
      show_source: false
      show_root_heading: true

::: edac.client.exceptions.EdacNotFoundError
    handler: python
    options:
      show_source: false
      show_root_heading: true

::: edac.client.exceptions.EdacRetryExhausted
    handler: python
    options:
      show_source: false
      show_root_heading: true

::: edac.client.exceptions.EdacStreamError
    handler: python
    options:
      show_source: false
      show_root_heading: true

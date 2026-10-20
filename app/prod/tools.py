"""Teaser for the Agentic Systems session: a tool's input schema is just a Pydantic model.

Run: uv run python -m app.prod.tools
"""

import json

from pydantic import BaseModel, Field


class LookupCustomerArgs(BaseModel):
    """Look up a customer's plan and recent orders before replying to a ticket."""

    customer_email: str = Field(description="Email address on the ticket")
    include_orders: bool = Field(default=True, description="Also return the last 5 orders")


if __name__ == "__main__":
    tool = {
        "name": "lookup_customer",
        "description": LookupCustomerArgs.__doc__,
        "input_schema": LookupCustomerArgs.model_json_schema(),
    }
    print(json.dumps(tool, indent=2))

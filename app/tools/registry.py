# AI tool implementation: registry.
# Model-callable clinic action; results stay internal, never dumped to WhatsApp.
from typing import Dict, List, Any, Optional
from app.tools.base import BaseTool
from app.tools.clinic_tools import (
    GetPatientTool,
    GetLeadTool,
    GetDoctorsTool,
    GetServicesTool,
    CheckAvailabilityTool,
    BookAppointmentTool,
    RescheduleAppointmentTool,
    FetchAppointmentTool,
    RequestHumanHandoffTool,
)
from app.tools.generic_tools import (
    EntityInfoTool,
    KnowledgeBaseSearchTool,
    FaqLookupTool,
)


# Tool registry.
class ToolRegistry:
    # Initialize instance.
    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}
        self._register_default_tools()

    # Register default tools.
    def _register_default_tools(self):
        # Register Clinic Tools
        self.register(GetPatientTool())
        self.register(GetLeadTool())
        self.register(GetDoctorsTool())
        self.register(GetServicesTool())
        self.register(CheckAvailabilityTool())
        self.register(BookAppointmentTool())
        self.register(RescheduleAppointmentTool())
        self.register(FetchAppointmentTool())
        self.register(RequestHumanHandoffTool())

        # Register Generic Tools
        self.register(EntityInfoTool())
        self.register(KnowledgeBaseSearchTool())
        self.register(FaqLookupTool())

    # Register.
    def register(self, tool: BaseTool):
        self._tools[tool.name] = tool

    # Get tool.
    def get_tool(self, name: str) -> Optional[BaseTool]:
        return self._tools.get(name)

    # Get tools for capabilities.
    def get_tools_for_capabilities(self, capabilities: List[str]) -> List[BaseTool]:
        return [tool for tool in self._tools.values() if tool.capability in capabilities]

    # Get openrouter schemas.
    def get_openrouter_schemas(
        self,
        capabilities: List[str],
        tool_names: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        tools = self.get_tools_for_capabilities(capabilities)
        if tool_names is not None:
            allow = set(tool_names)
            tools = [t for t in tools if t.name in allow]
        return [tool.to_openrouter_tool() for tool in tools]


tool_registry = ToolRegistry()

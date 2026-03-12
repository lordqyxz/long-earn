import re

from typing import Any, Dict, List

from langchain.tools import tool


@tool
class CodeSafetyCheck:
    """代码安全检查工具"""

    def __init__(self):
        # 定义安全检查规则
        self.dangerous_patterns = [
            r"eval\(",
            r"exec\(",
            r"__import__\(",
            r"open\(",
            r"file\(",
            r"os\.system\(",
            r"subprocess\.call\(",
            r"subprocess\.Popen\(",
            r"input\(",
            r"raw_input\(",
        ]

    def check(self, code: str) -> Dict[str, Any]:
        """检查代码安全性"""
        issues = []
        lines = code.split("\n")

        for i, line in enumerate(lines, 1):
            for pattern in self.dangerous_patterns:
                if re.search(pattern, line):
                    issues.append(
                        {
                            "line": i,
                            "content": line.strip(),
                            "issue": f"可能存在安全风险: {pattern}",
                        }
                    )

        return {"safe": len(issues) == 0, "issues": issues}

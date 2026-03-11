import logging
from typing import Dict, Any, Optional

logger = logging.getLogger('long_earn')

class ExceptionCallback:
    """异常处理回调函数"""
    def __init__(self):
        pass
    
    def handle_exception(self, error: Exception, state: Dict[str, Any]) -> Dict[str, Any]:
        """处理异常"""
        # 记录异常
        logger.error(f"处理异常: {str(error)}", exc_info=True)
        
        # 返回错误状态
        return {
            "error": str(error),
            "status": "error"
        }
    
    def recover_from_error(self, error: Exception, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """尝试从错误中恢复"""
        # 这里可以实现错误恢复逻辑
        # 例如，对于网络错误，可以尝试重试
        logger.info(f"尝试从错误中恢复: {str(error)}")
        return None

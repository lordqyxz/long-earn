import logging
from typing import Dict, Any

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    filename='logs/app.log'
)

logger = logging.getLogger('long_earn')

class LoggerCallback:
    """日志记录回调函数"""
    def __init__(self):
        pass
    
    def on_start(self, state: Dict[str, Any]) -> None:
        """任务开始时调用"""
        logger.info(f"任务开始: {state.get('user_query', '')}")
    
    def on_end(self, state: Dict[str, Any]) -> None:
        """任务结束时调用"""
        logger.info(f"任务结束: {state.get('summary', '')}")
    
    def on_error(self, error: Exception, state: Dict[str, Any]) -> None:
        """任务出错时调用"""
        logger.error(f"任务出错: {str(error)}", exc_info=True)
    
    def on_node_start(self, node_name: str, state: Dict[str, Any]) -> None:
        """节点开始时调用"""
        logger.info(f"节点开始: {node_name}")
    
    def on_node_end(self, node_name: str, state: Dict[str, Any]) -> None:
        """节点结束时调用"""
        logger.info(f"节点结束: {node_name}")

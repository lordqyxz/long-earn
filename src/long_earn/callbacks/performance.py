import time
import logging
from typing import Dict, Any

logger = logging.getLogger('long_earn')

class PerformanceCallback:
    """性能监控回调函数"""
    def __init__(self):
        self.start_times = {}
        self.node_times = {}
    
    def on_start(self, state: Dict[str, Any]) -> None:
        """任务开始时调用"""
        self.start_times['task'] = time.time()
    
    def on_end(self, state: Dict[str, Any]) -> None:
        """任务结束时调用"""
        if 'task' in self.start_times:
            duration = time.time() - self.start_times['task']
            logger.info(f"任务执行时间: {duration:.2f}秒")
            # 可以将性能数据存储到数据库或文件中
    
    def on_node_start(self, node_name: str, state: Dict[str, Any]) -> None:
        """节点开始时调用"""
        self.start_times[node_name] = time.time()
    
    def on_node_end(self, node_name: str, state: Dict[str, Any]) -> None:
        """节点结束时调用"""
        if node_name in self.start_times:
            duration = time.time() - self.start_times[node_name]
            self.node_times[node_name] = duration
            logger.info(f"节点 {node_name} 执行时间: {duration:.2f}秒")
    
    def get_performance_metrics(self) -> Dict[str, float]:
        """获取性能指标"""
        return self.node_times

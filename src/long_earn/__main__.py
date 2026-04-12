from dotenv import load_dotenv

from long_earn.agent import create_main_agent

load_dotenv()


def main():
    """主函数"""
    from long_earn.context_init import create_runtime_context

    # 创建运行时上下文
    context = create_runtime_context()

    # 初始化知识库（通过服务）
    context.knowledge_service.initialize()

    # 创建主 Agent
    agent = create_main_agent(context)

    # 使用示例
    # result = agent.invoke({"user_query": "分析贵州茅台"})
    # print(result)


if __name__ == "__main__":
    main()

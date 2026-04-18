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

    # 执行用户查询：分析净利润增长策略
    user_query = "分析净利润增长策略"
    context.logger.info(f"开始处理用户查询: {user_query}")
    print(f"正在处理: {user_query}\n")

    try:
        result = agent.invoke({"user_query": user_query})
        print("\n" + "=" * 60)
        print("分析结果:")
        print("=" * 60)
        print(result.get("summary", "无结果"))

        # 输出监控报告
        context.monitoring.log_report(context.logger)
    except Exception as e:
        context.logger.error(f"执行异常: {e}")
        print(f"\n执行过程中出现错误: {e}")


if __name__ == "__main__":
    main()

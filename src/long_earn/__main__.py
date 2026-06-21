from long_earn.agent import create_main_agent
from long_earn.context_init import initialize_context

# 配置加载由 initialize_context → load_config 统一处理（dotenv + AppConfig）。
# 详见 ADR-007 配置中心化。无需在此调用 load_dotenv()。


def main():
    """主函数"""
    context = initialize_context()

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

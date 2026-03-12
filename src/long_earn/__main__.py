from long_earn.agent import create_main_agent


def main():
    """主函数"""
    # 创建主图智能体
    agent = create_main_agent()

    # 测试策略查询
    result = agent.invoke({"user_query": "测试策略"})
    print(result)

    # 测试股票查询
    result = agent.invoke({"user_query": "测试股票"})
    print(result)


if __name__ == "__main__":
    main()

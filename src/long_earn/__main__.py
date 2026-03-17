from long_earn.agent import create_main_agent


def main():
    """主函数"""
    from long_earn.tools.store import init_system

    init_system()

    agent = create_main_agent()

    result = agent.invoke({"user_query": "测试策略"})
    print(result)

    result = agent.invoke({"user_query": "测试股票"})
    print(result)


if __name__ == "__main__":
    main()

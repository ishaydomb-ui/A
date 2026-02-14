def add(a, b):
    return a + b


def subtract(a, b):
    return a - b


def multiply(a, b):
    return a * b


def divide(a, b):
    if b == 0:
        raise ValueError("Cannot divide by zero")
    return a / b


OPERATIONS = {
    "+": add,
    "-": subtract,
    "*": multiply,
    "/": divide,
}


def calculate(a, op, b):
    if op not in OPERATIONS:
        raise ValueError(f"Unknown operator: {op}. Use one of: {', '.join(OPERATIONS)}")
    return OPERATIONS[op](a, b)


def main():
    print("Simple Calculator")
    print("Type 'quit' to exit.\n")

    while True:
        expr = input("Enter expression (e.g. 2 + 3): ").strip()
        if expr.lower() == "quit":
            break

        parts = expr.split()
        if len(parts) != 3:
            print("Invalid format. Use: <number> <operator> <number>")
            continue

        try:
            a = float(parts[0])
            op = parts[1]
            b = float(parts[2])
            result = calculate(a, op, b)
            print(f"= {result}\n")
        except ValueError as e:
            print(f"Error: {e}\n")


if __name__ == "__main__":
    main()

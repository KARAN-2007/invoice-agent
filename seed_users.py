from auth import create_user

# Demo accounts with made-up names. These passwords are for the demo only.
DEMO_USERS = [
    ("vendor1", "Demo Vendor", "vendor", "vendor123"),
    ("approver1", "Demo Approver", "approver", "approver123"),
    ("admin1", "Demo Admin", "admin", "admin123"),
]


def main():
    for username, full_name, role, password in DEMO_USERS:
        create_user(username, full_name, role, password)
        print("Ready: " + role + " account  " + username + " / " + password)


if __name__ == "__main__":
    main()
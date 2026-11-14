
from auth import create_user


# Demo accounts for the college project.
# These passwords are for demonstration only.

DEMO_USERS = [
    (
        "vendor1",
        "Demo Vendor",
        "vendor",
        "vendor123",
    ),
    (
        "procurement1",
        "Demo Procurement Officer",
        "procurement_officer",
        "procurement123",
    ),
    (
        "approver1",
        "Demo Approver",
        "approver",
        "approver123",
    ),
    (
        "admin1",
        "Demo Admin",
        "admin",
        "admin123",
    ),
]


def main():
    for username, full_name, role, password in DEMO_USERS:

        create_user(
            username,
            full_name,
            role,
            password,
        )

        print(
            "Ready: "
            + role
            + " account: "
            + username
        )

    print("\nAll four demo accounts are ready.")


if __name__ == "__main__":
    main()

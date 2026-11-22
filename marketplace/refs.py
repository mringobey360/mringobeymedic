"""Customer-facing short references.

The full UUID is the unguessable key that protects a customer's payment pages, so it is never
printed on screen. Customers and support use this short, readable code instead (e.g. SP-7CA55820).
"""


def short_reference(value):
    return 'SP-' + str(value).replace('-', '')[:8].upper()

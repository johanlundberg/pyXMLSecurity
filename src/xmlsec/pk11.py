import base64

__author__ = 'leifj'

from exceptions import XMLSigException
from urlparse import urlparse
import os
import logging

_modules = {}

try:
    import PyKCS11
    from PyKCS11.LowLevel import CKA_ID, CKA_LABEL, CKA_CLASS, CKO_PRIVATE_KEY, CKO_CERTIFICATE, CKK_RSA, CKA_KEY_TYPE, CKA_VALUE
except ImportError:
    raise XMLSigException("pykcs11 is required for PKCS#11 keys - cf README.rst")

all_attributes = PyKCS11.CKA.keys()

# remove the CKR_ATTRIBUTE_SENSITIVE attributes since we can't get
all_attributes.remove(PyKCS11.LowLevel.CKA_PRIVATE_EXPONENT)
all_attributes.remove(PyKCS11.LowLevel.CKA_PRIME_1)
all_attributes.remove(PyKCS11.LowLevel.CKA_PRIME_2)
all_attributes.remove(PyKCS11.LowLevel.CKA_EXPONENT_1)
all_attributes.remove(PyKCS11.LowLevel.CKA_EXPONENT_2)
all_attributes.remove(PyKCS11.LowLevel.CKA_COEFFICIENT)
all_attributes = [e for e in all_attributes if isinstance(e, int)]

def parse_uri(pk11_uri):
    o = urlparse(pk11_uri)
    if o.scheme != 'pkcs11':
        raise XMLSigException("Bad URI scheme in pkcs11 URI %s" % pk11_uri)

    slot = 0
    library = None
    keyname = None
    query = {}

    if not '/' in o.path:
        raise XMLSigException("Missing keyname part in pkcs11 URI (pkcs11://[library[:slot]/]keyname[?pin=<pin>])")

    (module_path,sep,keyqs) = o.path.rpartition('/')

    if '?' in keyqs:
        (keyname,sep,qs) = keyqs.rpartition('?')
        for av in qs.split('&'):
            if not '=' in av:
                raise XMLSigException("Bad query string in pkcs11 URI %s" % pk11_uri)
            (a,sep,v) = av.partition('=')
            assert(a)
            assert(v)
            query[a] = v
    else:
        keyname = keyqs

    if ':' in module_path:
        (library,sep,slot_str) = o.netloc.rpartition()
        slot = int(slot_str)
    else:
        library = module_path

    if library is None or len(library) == 0:
        library = os.environ.get('PYKCS11LIB',None)

    if library is None or len(library) == 0:
        raise XMLSigException("No PKCS11 module in pkcs11 URI %s" % pk11_uri)

    return library,slot,keyname,query

def _intarray2bytes(x):
    return ''.join(chr(i) for i in x)

def _sign_and_close(session,key,data,mech):
    logging.debug("signing %d bytes using %s" % (len(data),mech))
    #import pdb; pdb.set_trace()
    sig = session.sign(key,data,mech)
    session.logout()
    session.closeSession()

    return _intarray2bytes(sig)

def _find_object(session,template):
    for o in session.findObjects(template):
        logging.debug("Found pkcs11 object: %s" % o)
        return o
    return None

def _get_object_attributes(session,o):
    print all_attributes
    attributes = session.getAttributeValue(o, all_attributes)
    return dict(zip(all_attributes, attributes))

def _cert_der2pem(der):
    x = base64.standard_b64encode(der)
    r = "-----BEGIN CERTIFICATE-----\n"
    while len(x) > 64:
        r += x[0:64]
        r += "\n"
        x = x[64:]
    r += x
    r += "\n"
    r += "-----END CERTIFICATE-----"
    return r

def _find_key(session,keyname):
    key = _find_object(session,[(CKA_LABEL,keyname),(CKA_CLASS,CKO_PRIVATE_KEY),(CKA_KEY_TYPE,CKK_RSA)])
    if key is None:
        return None
    key_a = _get_object_attributes(session,key)
    cert = _find_object(session,[(CKA_ID,key_a[CKA_ID]),(CKA_CLASS,CKO_CERTIFICATE)])
    cert_pem = None
    if cert is not None:
        cert_a = _get_object_attributes(session,cert)
        cert_pem = _cert_der2pem(_intarray2bytes(cert_a[CKA_VALUE]))
        logging.debug(cert)
    return key,cert_pem

def signer(pk11_uri,mech=PyKCS11.MechanismRSAPKCS1):
    library,slot,keyname,query = parse_uri(pk11_uri)

    if not _modules.has_key(library):
        lib = PyKCS11.PyKCS11Lib()
        lib.load(library)
        _modules[library] = lib

    lib = _modules[library]
    session = lib.openSession(slot)

    pin = None
    pin_spec = query.get('pin',"env:PYKCS11PIN")
    if pin_spec.startswith("env:"):
        pin = os.environ.get(pin_spec[4:],None)
    else:
        pin = pin_spec

    if pin is not None:
        session.login(pin)
    else:
        logging.warning("No pin provided - not logging in")

    key,cert = _find_key(session,keyname)
    if key is None:
        raise XMLSigException("No such key: %s" % pk11_uri)

    if cert is not None:
        logging.info("Found matching cert in token")

    return lambda data: _sign_and_close(session,key,data,mech),cert

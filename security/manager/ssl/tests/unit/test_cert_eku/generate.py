#!/usr/bin/python

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# requires openssl >= 1.0.0

import tempfile, os, sys, random
libpath = os.path.abspath('../psm_common_py')

sys.path.append(libpath)

import CertUtils

srcdir = os.getcwd()
db = tempfile.mkdtemp()

CA_basic_constraints = "basicConstraints = critical, CA:TRUE\n"
EE_basic_constraints = "basicConstraints = CA:FALSE\n"

CA_full_ku = "keyUsage = keyCertSign, digitalSignature, nonRepudiation, keyEncipherment, dataEncipherment, keyAgreement, cRLSign\n"
EE_full_ku = "keyUsage = digitalSignature, nonRepudiation, keyEncipherment, dataEncipherment, keyAgreement\n"

key_type = 'rsa'

# codesigning differs significantly between mozilla::pkix and
# classic NSS that actual testing on it is not very useful
eku_values = { 'SA': "serverAuth",
               'CA': "clientAuth",
               #'CS': "codeSigning",
               'EP': "emailProtection",
               'TS': "timeStamping",
               'NS': "nsSGC", # Netscape Server Gated Crypto.
               'OS': "1.3.6.1.5.5.7.3.9"
             }

cert_usages = [ "certificateUsageSSLClient",
                "certificateUsageSSLServer",
                "certificateUsageSSLCA",
                "certificateUsageEmailSigner",
                "certificateUsageEmailRecipient",
                #"certificateUsageObjectSigner",
                "certificateUsageStatusResponder"
              ]

js_file_header = """//// AUTOGENERATED FILE, DO NOT EDIT
// -*- Mode: javascript; tab-width: 2; indent-tabs-mode: nil; c-basic-offset: 2 -*-
// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at http://mozilla.org/MPL/2.0/.

"use strict";

do_get_profile(); // must be called before getting nsIX509CertDB
const certdb = Cc["@mozilla.org/security/x509certdb;1"]
                 .getService(Ci.nsIX509CertDB);

function cert_from_file(filename) {
  let der = readFile(do_get_file("test_cert_eku/" + filename, false));
  return certdb.constructX509(der, der.length);
}

function load_cert(cert_name, trust_string) {
  var cert_filename = cert_name + ".der";
  addCertFromFile(certdb, "test_cert_eku/" + cert_filename, trust_string);
  return cert_from_file(cert_filename);
}

function run_test() {
  load_cert("ca", "CT,CT,CT");
"""

js_file_footer = """}
"""

def gen_int_js_output(int_string):
    expectedResult = "SEC_ERROR_INADEQUATE_CERT_TYPE"
    # For a certificate to verify successfully as a SSL CA, it must either
    # have no EKU or have the Server Auth or Netscape Server Gated Crypto
    # usage (the second of which is deprecated but currently supported for
    # compatibility purposes).
    if ("NONE" in int_string or "SA" in int_string or "NS" in int_string):
      expectedResult = "0"
    return ("  checkCertErrorGeneric(certdb, load_cert('" + int_string +
            "', ',,'), " + expectedResult + ", certificateUsageSSLCA);\n")

def single_test_output(ee_name, cert_usage, error):
    return ("  checkCertErrorGeneric(certdb, cert_from_file('" + ee_name +
            ".der'), " + error + ", " + cert_usage + ");\n")

def usage_to_abbreviation(usage):
    if usage is "certificateUsageStatusResponder":
        return "OS"
    if usage is "certificateUsageSSLServer":
        return "SA"
    if usage is "certificateUsageSSLClient":
        return "CA"
    if (usage is "certificateUsageEmailSigner" or
        usage is "certificateUsageEmailRecipient"):
        return "EP"
    raise Exception("unsupported usage: " + usage)

# In general, for a certificate to be compatible with a usage, it must either
# have no EKU at all or that usage must be present in its EKU extension.
def has_compatible_eku(name_string, usage_abbreviation):
    return ("NONE" in name_string or usage_abbreviation in name_string)

def gen_ee_js_output(int_string, ee_string, cert_usage, ee_name):
    if cert_usage is "certificateUsageSSLCA":
        # since none of these are CA certs (BC) these should all fail
        return single_test_output(ee_name, cert_usage,
                                  "SEC_ERROR_INADEQUATE_KEY_USAGE")

    usage_abbreviation = usage_to_abbreviation(cert_usage)
    if cert_usage is "certificateUsageStatusResponder":
        # For the Status Responder usage, the OSCP Signing usage must be
        # present in the end-entity's EKU extension (i.e. if the extension
        # is not present, the cert is not compatible with this usage).
        if "OS" not in ee_string:
            return single_test_output(ee_name, cert_usage,
                                      "SEC_ERROR_INADEQUATE_CERT_TYPE")
        if not has_compatible_eku(int_string, usage_abbreviation):
            return single_test_output(ee_name, cert_usage,
                                      "SEC_ERROR_INADEQUATE_CERT_TYPE")
        return single_test_output(ee_name, cert_usage, "0")

    # If the usage isn't Status Responder, if the end-entity certificate has
    # the OCSP Signing usage in its EKU, it is not valid for any other usage.
    if "OS" in ee_string:
        return single_test_output(ee_name, cert_usage,
                                  "SEC_ERROR_INADEQUATE_CERT_TYPE")

    if cert_usage is "certificateUsageSSLServer":
        if not has_compatible_eku(ee_string, usage_abbreviation):
            return single_test_output(ee_name, cert_usage,
                                      "SEC_ERROR_INADEQUATE_CERT_TYPE")
        # If the usage is SSL Server, the intermediate certificate must either
        # have no EKU extension or it must have the Server Auth or Netscape
        # Server Gated Crypto (deprecated but allowed for compatibility).
        if ("SA" not in int_string and "NONE" not in int_string and
            "NS" not in int_string):
            return single_test_output(ee_name, cert_usage,
                                      "SEC_ERROR_INADEQUATE_CERT_TYPE")
        return single_test_output(ee_name, cert_usage, "0")

    if not has_compatible_eku(ee_string, usage_abbreviation):
        return single_test_output(ee_name, cert_usage,
                                  "SEC_ERROR_INADEQUATE_CERT_TYPE")
    if not has_compatible_eku(int_string, usage_abbreviation):
        return single_test_output(ee_name, cert_usage,
                                  "SEC_ERROR_INADEQUATE_CERT_TYPE")

    return single_test_output(ee_name, cert_usage, "0")

def generate_test_eku():
    outmap = { "NONE" : ""}
    # add each one by itself
    for eku_name in (eku_values.keys()):
        outmap[eku_name] = eku_values[eku_name]
    # now combo of duples
    eku_names = sorted(eku_values.keys())
    for i in range(len(eku_names)):
        for j in range(i + 1, len(eku_names)):
            name = eku_names[i] + "_" + eku_names[j]
            outmap[name] = (eku_values[eku_names[i]] + "," +
                            eku_values[eku_names[j]])
    all_names = eku_names[0]
    all_values = eku_values[eku_names[0]]
    for i in range (1, len(eku_names)):
        all_names = all_names + "_" + eku_names[i]
        all_values = all_values + ", " + eku_values[eku_names[i]]
    outmap[all_names] = all_values
    return outmap

def generate_certs(do_cert_generation):
    js_outfile = open("../test_cert_eku.js", 'w')
    ca_name = "ca"
    if do_cert_generation:
        [ca_key, ca_cert] = CertUtils.generate_cert_generic(
                              db, srcdir, 1, key_type, ca_name,
                              CA_basic_constraints)
    ee_ext_text = EE_basic_constraints + EE_full_ku

    js_outfile.write(js_file_header)

    # now we do it again for valid basic constraints but strange eku/ku at the
    # intermediate layer
    eku_dict = generate_test_eku()
    print eku_dict
    for eku_name in (sorted(eku_dict.keys())):
        # generate int
        int_name = "int-EKU-" + eku_name
        int_serial = random.randint(100, 40000000)
        eku_text = "extendedKeyUsage = " + eku_dict[eku_name]
        if (eku_name == "NONE"):
            eku_text = ""
        int_ext_text = CA_basic_constraints + CA_full_ku + eku_text
        if do_cert_generation:
            [int_key, int_cert] = CertUtils.generate_cert_generic(
                                    db, srcdir, int_serial, key_type, int_name,
                                    int_ext_text, ca_key, ca_cert)
        js_outfile.write("\n")
        js_outfile.write(gen_int_js_output(int_name))

        for ee_eku_name in (sorted(eku_dict.keys())):
            ee_base_name = "ee-EKU-" + ee_eku_name
            ee_name = ee_base_name + "-" + int_name
            ee_serial = random.randint(100, 40000000)
            ee_eku = "extendedKeyUsage = critical," + eku_dict[ee_eku_name]
            if (ee_eku_name == "NONE"):
                ee_eku = ""
            ee_ext_text = EE_basic_constraints + EE_full_ku + ee_eku
            if do_cert_generation:
                [ee_key, ee_cert] = CertUtils.generate_cert_generic(
                                      db, srcdir, ee_serial, key_type, ee_name,
                                      ee_ext_text, int_key, int_cert)
            for cert_usage in (cert_usages):
                js_outfile.write(gen_ee_js_output(int_name, ee_base_name,
                                 cert_usage, ee_name))

    js_outfile.write(js_file_footer)
    js_outfile.close()

# By default, re-generate the certificates. Anything can be passed as a
# command-line option to prevent this.
do_cert_generation = len(sys.argv) < 2
generate_certs(do_cert_generation)
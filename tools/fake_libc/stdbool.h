#ifndef PFC_FAKE_STDBOOL_H
#define PFC_FAKE_STDBOOL_H

/* Parsing-only C11 spellings; no host ABI or library declarations are modeled. */
#define bool _Bool
#define true 1
#define false 0
#define __bool_true_false_are_defined 1

#endif

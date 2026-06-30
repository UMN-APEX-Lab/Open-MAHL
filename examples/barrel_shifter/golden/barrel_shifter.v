// Golden (trusted, human-written) reference implementation of the 8-bit logical
// barrel shifter described in ../prompt.txt.
//
// Convention (unambiguous): direction 0 = logical LEFT, 1 = logical RIGHT,
// zeros shifted in, overflow discarded.
module barrel_shifter (
    input  [7:0] data_in,
    input  [2:0] shift_amount,
    input        direction,
    output [7:0] data_out
);
    assign data_out = (direction == 1'b0) ? (data_in << shift_amount)
                                          : (data_in >> shift_amount);
endmodule
